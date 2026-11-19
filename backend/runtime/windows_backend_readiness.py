"""Inspect Windows Python 3.13 and prepare hash-pinned offline install inputs."""

import argparse
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from email.parser import BytesHeaderParser
import hashlib
import hmac
import importlib
from importlib import metadata
from importlib.machinery import PathFinder
import ipaddress
import json
import os
from pathlib import Path
import re
from shutil import rmtree
import stat
import struct
import subprocess
import sys
import sysconfig
from tempfile import NamedTemporaryFile, TemporaryDirectory
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZipFile


# Direct startup dependencies in requirements.txt, plus imported transitive
# packages. This is a minimum-floor check, not an offline resolver or lockfile.
_STARTUP = (
    ('fastapi', 'fastapi', (0, 115)), ('uvicorn', 'uvicorn', (0, 30)),
    ('python-multipart', 'python_multipart', (0, 0, 9)),
    ('pydantic-settings', 'pydantic_settings', (2, 4)), ('PyYAML', 'yaml', (6,)),
    ('httpx', 'httpx', (0, 27)), ('argon2-cffi', 'argon2', (23, 1)), ('psutil', 'psutil', (5, 9)),
    ('pydantic', 'pydantic', (2,)), ('starlette', 'starlette', ()),
    ('pydantic-core', 'pydantic_core', ()), ('argon2-cffi-bindings', '_argon2_cffi_bindings', ()),
    ('cffi', '_cffi_backend', ()),
)
_EXTENSIONS = ('_sqlite3', '_ssl', '_socket', '_overlapped')
_DLLS = ('kernel32.dll', 'advapi32.dll', 'ntdll.dll', 'secur32.dll', 'cldapi.dll')
_RELEASE = re.compile(r'([0-9]{1,6}(?:\.[0-9]{1,6}){0,3})(?:\.post[0-9]{1,6})?')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_WHEEL = re.compile(r'([A-Za-z0-9_]+)-([0-9][A-Za-z0-9.!+_]*)(?:-[0-9][A-Za-z0-9_]*)?'
                    r'-([a-z0-9]+(?:\.[a-z0-9]+)*)-([a-z0-9]+(?:\.[a-z0-9]+)*)'
                    r'-([a-z0-9_]+(?:\.[a-z0-9_]+)*)\.whl')
_MAX_LOCK_BYTES = 64 * 1024
_MAX_WHEELS = 256
_MAX_WHEEL_BYTES = 128 * 1024 * 1024
_MAX_WHEELSET_BYTES = 512 * 1024 * 1024
_MAX_WHEEL_METADATA_HEADER_BYTES = 64 * 1024
_REQUIREMENT_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*')


class _WheelsetError(ValueError):
    """Categorical diagnostic with no local paths or artifact contents."""


@dataclass(frozen=True)
class _WheelDependencies:
    requires_python: str
    extras: frozenset[str]
    requirements: tuple[str, ...]


def _stable_release(version):
    match = _RELEASE.fullmatch(version or '')
    return (tuple(map(int, match[1].split('.'))) + (0,) * 4)[:4] if match else None


def _windows_python_313_64(runtime, bits, platform):
    return platform == 'win32' and runtime.version_info[:2] == (3, 13) and bits == 64


def _wheel_target_issues(platform_tag):
    if platform_tag != 'win-amd64':
        return ['WINDOWS_AMD64_RUNTIME_REQUIRED']
    if sys.implementation.name != 'cpython' or sysconfig.get_config_var('Py_GIL_DISABLED'):
        return ['STANDARD_CPYTHON_ABI_REQUIRED']
    return []


def require_supervised_interpreter():
    if (not _windows_python_313_64(sys, 8 * struct.calcsize('P'), sys.platform)
            or _wheel_target_issues(sysconfig.get_platform())):
        raise RuntimeError('Supervised startup requires standard Windows CPython 3.13 x64')


def _interpreter_issues(runtime, bits, platform):
    issues = []
    if not _windows_python_313_64(runtime, bits, platform):
        issues.append('WINDOWS_PYTHON_313_64_REQUIRED')
    if not runtime.flags.isolated or not runtime.flags.dont_write_bytecode:
        issues.append('ISOLATED_NO_BYTECODE_REQUIRED')
    try:
        if not Path(runtime.executable).samefile(runtime._base_executable):
            issues.append('DIRECT_INTERPRETER_REQUIRED')
    except (AttributeError, OSError, ValueError):
        issues.append('INTERPRETER_IDENTITY_UNVERIFIED')
    if (len(runtime.path) > 32 or any(not re.fullmatch('[A-Za-z]:', Path(entry).drive)
                                     or not Path(entry).is_absolute() for entry in runtime.path)):
        issues.append('LOCAL_IMPORT_PATH_REQUIRED')
    return issues


def _dependency_issues():
    missing, incompatible, unverified = [], [], []
    for dependency, module, minimum in _STARTUP:
        try:
            version = metadata.version(dependency)
            available = PathFinder.find_spec(module) is not None
        except metadata.PackageNotFoundError:
            missing.append(dependency)
            continue
        except (OSError, ValueError, ImportError):
            unverified.append(dependency)
            continue
        if not available:
            missing.append(dependency)
            continue
        installed = _stable_release(version)
        if installed is None:
            unverified.append(dependency)
        elif installed < (minimum + (0,) * 4)[:4]:
            incompatible.append({'package': dependency, 'installed': version,
                                 'minimum': '.'.join(map(str, minimum))})
    return missing, incompatible, unverified


def _native_issues():
    extensions = []
    for name in _EXTENSIONS:
        try:
            importlib.import_module(name)
        except (ImportError, OSError):
            extensions.append(name)
    dlls = []
    try:
        import ctypes
    except (ImportError, OSError):
        return extensions + ['_ctypes'], None
    for name in _DLLS:
        try:
            ctypes.WinDLL(name, winmode=0x800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
        except OSError:
            dlls.append(name)
    return extensions, dlls


def _unsafe_environment(environment):
    # Report variable names only. Environment values may contain credentials.
    values = {key.upper(): value for key, value in environment.items()}
    unsafe = [key for key in ('ALL_PROXY', 'HTTPS_PROXY', 'HTTP_PROXY', 'PIP_PROXY',
                              'PIP_INDEX_URL', 'PIP_EXTRA_INDEX_URL', 'PIP_FIND_LINKS',
                              'PIP_REQUIREMENT', 'PIP_CONSTRAINT', 'PIP_BUILD_CONSTRAINT')
              if values.get(key)]
    if values.get('PIP_CONFIG_FILE') and values['PIP_CONFIG_FILE'].casefold() != os.devnull.casefold():
        unsafe.append('PIP_CONFIG_FILE')
    checks = {
        'BACKEND_HOST': {'127.0.0.1', '::1'}, 'AUTH_ENABLED': {'true', '1'},
        'AUTH_DEV_OVERRIDE': {'false', '0'}, 'LOCAL_CHAT_ONLY': {'true', '1'},
        'APP_ENV': {'development', 'dev'}, 'ENVIRONMENT': {'development', 'dev'},
        'PROVENANCE_KEY_MODE': {'ephemeral'},
    }
    unsafe.extend(key for key, allowed in checks.items() if key in values and values[key].lower() not in allowed)
    if 'OLLAMA_BASE_URL' in values:
        try:
            url = urlsplit(values['OLLAMA_BASE_URL'])
            if (url.scheme != 'http' or url.username is not None or url.password is not None
                    or not ipaddress.ip_address(url.hostname).is_loopback
                    or url.path not in ('', '/') or url.query or url.fragment or not url.port):
                unsafe.append('OLLAMA_BASE_URL')
        except ValueError:
            unsafe.append('OLLAMA_BASE_URL')
    return sorted(unsafe)


def inspect_runtime():
    bits = 8 * struct.calcsize('P')
    platform_tag = sysconfig.get_platform()
    interpreter = _interpreter_issues(sys, bits, sys.platform)
    if sys.platform == 'win32':
        interpreter.extend(_wheel_target_issues(platform_tag))
    # Decline to search packages or load DLLs in an unverified interpreter.
    missing, incompatible, unverified = _dependency_issues() if not interpreter else (None, None, None)
    extensions, dlls = _native_issues() if not interpreter else (None, None)
    return {
        'status': 'BLOCKED', 'python': '.'.join(map(str, sys.version_info[:3])), 'pointer_bits': bits,
        'platform_tag': platform_tag if platform_tag in ('win-amd64', 'win-arm64', 'win32') else 'UNVERIFIED',
        'interpreter_issues': interpreter, 'missing_packages': missing, 'incompatible_packages': incompatible,
        'unverified_versions': unverified, 'missing_native_extensions': extensions, 'missing_system_dlls': dlls,
        'unsafe_environment_overrides': _unsafe_environment(os.environ),
        'filtered_token_provider': 'UNAVAILABLE_IN_STANDALONE_PROBE',
        'resolved_application_profile': 'UNVERIFIED', 'application_import': 'NOT_ATTEMPTED',
        'listener_acceptance': 'NOT_ATTEMPTED',
    }


def _local_path(path):
    if not path.is_absolute() or '..' in path.parts or len(path.parts) > 32:
        raise _WheelsetError('LOCAL_ARTIFACT_PATH_REQUIRED')
    if os.name == 'nt':
        import ctypes

        if not re.fullmatch('[A-Za-z]:', path.drive) or any(os.path.isreserved(part) for part in path.parts[1:]):
            raise _WheelsetError('LOCAL_ARTIFACT_PATH_REQUIRED')
        drive_type = ctypes.WinDLL('kernel32.dll', winmode=0x800).GetDriveTypeW
        drive_type.argtypes = [ctypes.c_wchar_p]
        drive_type.restype = ctypes.c_uint
        if drive_type(path.anchor) not in (2, 3):  # Local removable or fixed storage.
            raise _WheelsetError('LOCAL_ARTIFACT_PATH_REQUIRED')
    for item in (*reversed(path.parents), path):
        info = item.lstat()
        if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x00441440
                or (item != path and not stat.S_ISDIR(info.st_mode))):
            raise _WheelsetError('UNSAFE_ARTIFACT_PATH')
    return info


@contextmanager
def _artifact(path, limit):
    info = _local_path(path)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= limit:
        raise _WheelsetError('ARTIFACT_SIZE_OR_TYPE_INVALID')
    with path.open('rb') as stream:
        observed = os.fstat(stream.fileno())
        if _file_stamp(info) != _file_stamp(observed):
            raise _WheelsetError('ARTIFACT_CHANGED')
        yield stream, info.st_size
        if _file_stamp(info) != _file_stamp(path.lstat()):
            raise _WheelsetError('ARTIFACT_CHANGED')


def _file_stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)


def _wheel_directory_entries(directory):
    present = set()
    with os.scandir(directory) as entries:
        for count, entry in enumerate(entries, 1):
            if count > _MAX_WHEELS:
                raise _WheelsetError('WHEEL_DIRECTORY_LIMIT')
            present.add(entry.name)
    return present


def _unique_members(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise _WheelsetError('INVALID_WHEEL_LOCK')
    return result


def _compatible_tags(python, abi, platform):
    python, abi, platform = python.split('.'), abi.split('.'), platform.split('.')
    if 'none' in abi and ('any' in platform or 'win_amd64' in platform):
        if {'py3', 'py313', 'cp313'}.intersection(python):
            return True
    return ('win_amd64' in platform and (('cp313' in python and 'cp313' in abi) or
            ('abi3' in abi and any(re.fullmatch(r'cp3(?:[2-9]|1[0-3])', tag) for tag in python))))


def _reviewed_lock(path, approved_sha256):
    if type(approved_sha256) is not str or not _SHA256.fullmatch(approved_sha256):
        raise _WheelsetError('REVIEWED_LOCK_DIGEST_REQUIRED')
    with _artifact(path, _MAX_LOCK_BYTES) as (stream, size):
        raw = stream.read(size + 1)
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), approved_sha256):
        raise _WheelsetError('WHEEL_LOCK_DIGEST_MISMATCH')
    lock = json.loads(raw.decode('ascii'), object_pairs_hook=_unique_members)
    if (type(lock) is not dict or set(lock) != {'schema', 'target', 'wheels'} or type(lock['schema']) is not int
            or lock['schema'] != 1 or lock['target'] != 'cp313-win_amd64' or type(lock['wheels']) is not dict
            or not 1 <= len(lock['wheels']) <= _MAX_WHEELS):
        raise _WheelsetError('INVALID_WHEEL_LOCK')
    return lock['wheels']


def _project_name(value):
    return re.sub(r'[-_.]+', '-', value.lower())


def _wheel_requirements(stream, project, version):
    """Inspect only bounded metadata from a wheel whose bytes match its review pin."""
    try:
        with ZipFile(stream) as archive:
            entries = [item for item in archive.infolist()
                       if item.filename.count('/') == 1 and item.filename.endswith('.dist-info/METADATA')]
            if len(entries) != 1 or not entries[0].file_size or entries[0].flag_bits & 1:
                raise _WheelsetError('INVALID_WHEEL_METADATA')
            stem = entries[0].filename.removesuffix('.dist-info/METADATA')
            metadata_project, _, metadata_version = stem.rpartition('-')
            if _project_name(metadata_project) != project or metadata_version != version:
                raise _WheelsetError('INVALID_WHEEL_METADATA')
            raw = bytearray()
            with archive.open(entries[0]) as metadata_stream:
                while True:
                    remaining = _MAX_WHEEL_METADATA_HEADER_BYTES - len(raw)
                    line = metadata_stream.readline(remaining + 1)
                    if len(line) > remaining:
                        raise _WheelsetError('INVALID_WHEEL_METADATA')
                    if not line or line in (b'\n', b'\r\n'):
                        break
                    raw.extend(line)
        meta = BytesHeaderParser().parsebytes(bytes(raw))
        names = meta.get_all('Name', [])
        formats = meta.get_all('Metadata-Version', [])
        if (meta.defects or len(formats) != 1 or not isinstance(formats[0], str)
                or not re.fullmatch(r'[12]\.\d+', formats[0])
                or len(names) != 1 or not isinstance(names[0], str) or not _REQUIREMENT_NAME.fullmatch(names[0])
                or _project_name(names[0]) != project or meta.get_all('Version') != [version]):
            raise _WheelsetError('INVALID_WHEEL_METADATA')
        python_constraints = meta.get_all('Requires-Python', [])
        extras = meta.get_all('Provides-Extra', [])
        if (len(python_constraints) > 1
                or any(not isinstance(value, str) or not value.isascii() for value in python_constraints)
                or any(not isinstance(value, str) or not _REQUIREMENT_NAME.fullmatch(value) for value in extras)):
            raise _WheelsetError('INVALID_WHEEL_METADATA')
        required = []
        for requirement in meta.get_all('Requires-Dist', []):
            if not isinstance(requirement, str) or not requirement.isascii():
                raise _WheelsetError('INVALID_WHEEL_METADATA')
            if '@' in requirement:
                # Direct references can escape pip's --no-index policy.
                raise _WheelsetError('DIRECT_WHEEL_DEPENDENCY_FORBIDDEN')
            required.append(re.sub(r'\r?\n[ \t]+', ' ', requirement))
        return _WheelDependencies(python_constraints[0] if python_constraints else '',
                                  frozenset(map(_project_name, extras)), tuple(required))
    except (BadZipFile, EOFError, NotImplementedError, RuntimeError):
        raise _WheelsetError('INVALID_WHEEL_METADATA') from None


def _verify_dependency_closure(pins, dependencies, result):
    # Use the selected review interpreter's pip-bundled PEP 440/508 parser.
    # This does not invoke pip, and wheel code is never imported.
    try:
        from pip._vendor.packaging.markers import UndefinedComparison, UndefinedEnvironmentName, default_environment
        from pip._vendor.packaging.requirements import Requirement
        from pip._vendor.packaging.specifiers import SpecifierSet
        from pip._vendor.packaging.version import Version
    except ImportError:
        raise _WheelsetError('OFFLINE_PACKAGING_PARSER_UNAVAILABLE') from None
    try:
        require_supervised_interpreter()
    except RuntimeError:
        raise _WheelsetError('INSTALL_REVIEW_TARGET_UNSUPPORTED') from None
    environment = default_environment()
    try:
        versions = {name: Version(pin[0]) for name, pin in pins.items()}
        requirements = {name: tuple(map(Requirement, wheel.requirements)) for name, wheel in dependencies.items()}
        incompatible_python = sorted(
            name for name, wheel in dependencies.items()
            if not SpecifierSet(wheel.requires_python).contains(environment['python_full_version'])
        )
        if incompatible_python:
            result['incompatible_python_packages'] = incompatible_python
            raise _WheelsetError('WHEELSET_REQUIRES_DIFFERENT_PYTHON')

        # Every pin is written as a direct installer requirement. Start each
        # with empty extras, then follow extras requested by active edges.
        pending = deque((name, '') for name in sorted(versions))
        queued = set(pending)
        missing, unconditional, conflicts = set(), set(), set()
        while pending:
            parent, extra = pending.popleft()
            marker_values = {**environment, 'extra': extra}
            for requirement in requirements[parent]:
                if requirement.marker is not None and not requirement.marker.evaluate(marker_values):
                    continue
                child = _project_name(requirement.name)
                if child not in versions:
                    missing.add((parent, child))
                    if requirement.marker is None:
                        unconditional.add(child)
                    continue
                if not requirement.specifier.contains(versions[child], prereleases=True):
                    conflicts.add((parent, child, 'VERSION'))
                for requested in sorted(map(_project_name, requirement.extras)):
                    if requested not in dependencies[child].extras:
                        conflicts.add((parent, child, 'EXTRA'))
                    elif (child, requested) not in queued:
                        queued.add((child, requested))
                        pending.append((child, requested))
        if missing:
            result['missing_dependencies'] = sorted(missing)
            if unconditional:
                result['missing_unconditional_dependencies'] = sorted(unconditional)
        if conflicts:
            result['dependency_conflicts'] = sorted(conflicts)
        if missing or conflicts:
            raise _WheelsetError('WHEELSET_INCOMPLETE_DEPENDENCY_CLOSURE' if missing
                                 else 'WHEELSET_DEPENDENCY_CONFLICT')
    except _WheelsetError:
        raise
    except (ValueError, UndefinedComparison, UndefinedEnvironmentName):
        raise _WheelsetError('INVALID_WHEEL_METADATA') from None
    result['dependency_closure'] = 'VERIFIED_FOR_TARGET'
    result['dependency_target_python'] = environment['python_full_version']


def _install_output_path(destination, wheel_directory):
    parent_info = _install_directory(destination.parent)
    target = destination.parent.resolve(strict=True) / destination.name
    _install_directory(target.parent, expected=parent_info)
    protected = (Path(__file__).resolve().parents[2], Path(sys.prefix).resolve(),
                 Path(sys.base_prefix).resolve(), wheel_directory.resolve(strict=True))
    if any(target.is_relative_to(root) for root in protected):
        raise _WheelsetError('UNSAFE_INSTALL_PREPARATION_PATH')
    return target


def _install_directory(path, *, expected=None):
    info = _local_path(path)
    if not stat.S_ISDIR(info.st_mode):
        raise _WheelsetError('UNSAFE_INSTALL_PREPARATION_PATH')
    if expected is not None and not os.path.samestat(expected, info):
        raise _WheelsetError('INSTALL_DIRECTORY_CHANGED')
    return info


@contextmanager
def _disposable_install_stage(parent, parent_info):
    # Keep ownership of cleanup tied to the directory created by this call.
    # If the parent or stage is replaced, leave the ambiguous path alone.
    _install_directory(parent, expected=parent_info)
    with TemporaryDirectory(prefix='.offline-install-', dir=parent, delete=False) as workspace:
        stage = Path(workspace)
        stage_info = _install_directory(stage)
        try:
            yield stage, stage_info
        finally:
            _install_directory(parent, expected=parent_info)
            _install_directory(stage, expected=stage_info)
            if stage.resolve(strict=True).parent != parent:
                raise _WheelsetError('INSTALL_DIRECTORY_CHANGED')
            rmtree(stage)


def _write_install_requirements(destination, wheel_directory, pins, lock_digest):
    if destination.name != 'requirements-offline.txt':
        raise _WheelsetError('OFFLINE_REQUIREMENTS_FILENAME_REQUIRED')
    target = _install_output_path(destination, wheel_directory)
    rows = ['# Reviewed wheel lock SHA-256 ' + lock_digest, '--no-index',
            '--only-binary :all:', '--require-hashes']
    rows.extend(f'{name}=={version} --hash=sha256:{digest}'
                for name, (version, digest) in sorted(pins.items()))
    temporary = None
    try:
        with NamedTemporaryFile(dir=target.parent, prefix='.offline-requirements-', mode='w',
                                encoding='ascii', newline='\n', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write('\n'.join(rows) + '\n')
        # Windows rename refuses an existing destination. POSIX rename would replace it.
        if os.name == 'nt':
            temporary.rename(target)
            temporary = None
        else:
            os.link(temporary, target)
    except FileExistsError:
        raise _WheelsetError('INSTALL_REQUIREMENTS_ALREADY_EXISTS') from None
    except OSError:
        raise _WheelsetError('INSTALL_REQUIREMENTS_WRITE_FAILED') from None
    finally:
        if temporary is not None:
            temporary.unlink()


_OFFLINE_PIP = """
import runpy
import sys

def offline(event, _args):
    if event in {
        'socket.connect', 'socket.bind', 'socket.sendto', 'socket.sendmsg',
        'socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr',
        'socket.getnameinfo',
        'subprocess.Popen', 'os.system', 'os.startfile', 'os.startfile/2',
    }:
        raise RuntimeError('Operation unavailable during offline installation')

sys.addaudithook(offline)
sys.path.append(sys.argv.pop(1))
runpy.run_module('pip', run_name='__main__', alter_sys=True)
"""


def _run_local_installer(stage, wheels, requirements, packages):
    # The selected direct interpreter supplies pip. Pass no inherited package
    # configuration, proxy variables, indexes or additional artifact sources.
    if not os.environ.get('SystemRoot'):
        raise _WheelsetError('WINDOWS_INSTALL_ENVIRONMENT_UNAVAILABLE')
    environment = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR') if key in os.environ}
    environment.update(PIP_CONFIG_FILE=os.devnull, TEMP=str(stage), TMP=str(stage))
    pip_root = Path(sysconfig.get_path('purelib'))
    if not stat.S_ISDIR(_local_path(pip_root).st_mode) or not pip_root.is_relative_to(Path(sys.base_prefix)):
        raise _WheelsetError('OFFLINE_INSTALLER_LOCATION_UNVERIFIED')
    command = [sys.executable, '-I', '-S', '-B', '-c', _OFFLINE_PIP, str(pip_root), '--isolated',
               '--disable-pip-version-check', '--no-cache-dir', '--no-input', 'install',
               '--no-index', '--only-binary=:all:', '--require-hashes', '--ignore-installed',
               '--find-links', str(wheels), '-r', str(requirements)]
    try:
        for options in (('--dry-run',), ('--no-deps', '--no-compile', '--target', str(packages))):
            subprocess.run([*command, *options], cwd=stage, env=environment, check=True, timeout=120,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        raise _WheelsetError('OFFLINE_INSTALLER_FAILED') from None


def _install_disposable(destination, directory, wheels, pins, lock_digest):
    if os.name != 'nt' or _interpreter_issues(sys, 8 * struct.calcsize('P'), sys.platform):
        raise _WheelsetError('DIRECT_ISOLATED_INSTALL_INTERPRETER_REQUIRED')
    target = _install_output_path(destination, directory)
    try:
        _local_path(destination)
    except FileNotFoundError:
        pass
    else:
        raise _WheelsetError('DISPOSABLE_INSTALL_DESTINATION_EXISTS')

    from pip._vendor.packaging.version import Version

    parent_info = _install_directory(target.parent)
    try:
        with _disposable_install_stage(target.parent, parent_info) as (stage, stage_info):
            wheel_copy, payload = stage / 'wheels', stage / 'payload'
            wheel_copy.mkdir()
            payload.mkdir()
            payload_info = _install_directory(payload)
            requirements = payload / 'requirements-offline.txt'
            packages = payload / 'site-packages'
            remaining = _MAX_WHEELSET_BYTES
            for filename in sorted(wheels):
                with _artifact(directory / filename, min(remaining, _MAX_WHEEL_BYTES)) as (source, size):
                    remaining -= size
                    copied = wheel_copy / filename
                    with copied.open('xb') as output:
                        unread = size
                        while unread:
                            chunk = source.read(min(unread, 1024 * 1024))
                            if not chunk:
                                raise _WheelsetError('ARTIFACT_CHANGED')
                            output.write(chunk)
                            unread -= len(chunk)
                with _artifact(copied, size) as (stream, _):
                    if not hmac.compare_digest(hashlib.file_digest(stream, 'sha256').hexdigest(), wheels[filename]):
                        raise _WheelsetError('WHEEL_DIGEST_MISMATCH')
            _write_install_requirements(requirements, directory, pins, lock_digest)
            _run_local_installer(stage, wheel_copy, requirements, packages)
            _install_directory(target.parent, expected=parent_info)
            _install_directory(stage, expected=stage_info)
            _install_directory(payload, expected=payload_info)
            package_info = _install_directory(packages)
            installed = {}
            for item in metadata.distributions(path=[str(packages)]):
                name = _project_name(item.metadata.get('Name') or '')
                if (name not in pins or name in installed or not isinstance(item.version, str)
                        or Version(item.version) != Version(pins[name][0])):
                    raise _WheelsetError('INSTALLED_DISTRIBUTION_MISMATCH')
                installed[name] = item.version
            if installed.keys() != pins.keys():
                raise _WheelsetError('INSTALLED_DISTRIBUTION_MISMATCH')
            _install_directory(packages, expected=package_info)
            _install_directory(payload, expected=payload_info)
            _install_directory(target.parent, expected=parent_info)
            # Win32 no-replace rename publishes only the verified payload.
            payload.rename(target)
            return dict(sorted(installed.items()))
    except _WheelsetError:
        raise
    except FileExistsError:
        raise _WheelsetError('DISPOSABLE_INSTALL_DESTINATION_EXISTS') from None
    except ValueError:
        raise _WheelsetError('INSTALLED_DISTRIBUTION_MISMATCH') from None
    except OSError:
        raise _WheelsetError('DISPOSABLE_INSTALL_FAILED') from None


def verify_wheelset(directory: Path, lock_path: Path, *, approved_lock_sha256: str,
                    install_requirements: Path | None = None, install_destination: Path | None = None) -> dict:
    """Verify a local wheel snapshot against a separately supplied review pin."""
    result = {'status': 'BLOCKED', 'target': 'cp313-win_amd64', 'missing_artifacts': [],
              'missing_startup_packages': [], 'incompatible_artifacts': [], 'mismatched_artifacts': [],
              'unreviewed_entries': 0, 'blockers': []}
    try:
        if install_requirements is not None and install_destination is not None:
            raise _WheelsetError('SELECT_ONE_INSTALL_OPERATION')
        prepare = install_requirements is not None or install_destination is not None
        wheels = _reviewed_lock(lock_path, approved_lock_sha256)
        floors = {name.lower(): minimum for name, _, minimum in _STARTUP}
        pins = {}
        identities = {}
        for filename, digest in wheels.items():
            match = _WHEEL.fullmatch(filename) if len(filename) <= 180 else None
            if match is None or type(digest) is not str or not _SHA256.fullmatch(digest):
                raise _WheelsetError('INVALID_WHEEL_LOCK')
            project, version, python, abi, platform = match.groups()
            project = _project_name(project)
            if project in pins:
                raise _WheelsetError('DUPLICATE_LOCKED_PROJECT')
            pins[project] = (version, digest)
            identities[filename] = (project, version)
            installed = _stable_release(version)
            minimum = floors.get(project)
            if (not _compatible_tags(python, abi, platform)
                    or (minimum is not None and (installed is None or installed < (minimum + (0,) * 4)[:4]))):
                result['incompatible_artifacts'].append(filename)
        result['missing_startup_packages'] = sorted(floors.keys() - pins.keys())
        directory_info = _local_path(directory)
        if not stat.S_ISDIR(directory_info.st_mode):
            raise _WheelsetError('LOCAL_WHEEL_DIRECTORY_REQUIRED')
        present = _wheel_directory_entries(directory)
        result['missing_artifacts'] = sorted(wheels.keys() - present)
        result['unreviewed_entries'] = len(present - wheels.keys())
        if any(result[name] for name in ('missing_artifacts', 'missing_startup_packages',
                                         'incompatible_artifacts', 'unreviewed_entries')):
            result['incompatible_artifacts'].sort()
            result['blockers'].append('WHEELSET_INCOMPLETE_OR_UNREVIEWED')
            return result
        remaining = _MAX_WHEELSET_BYTES
        verified = {}
        dependencies = {}
        for filename in sorted(wheels):
            with _artifact(directory / filename, min(_MAX_WHEEL_BYTES, remaining)) as (stream, size):
                verified[filename] = _file_stamp(os.fstat(stream.fileno()))
                digest = hashlib.sha256()
                unread = size
                while unread:
                    chunk = stream.read(min(unread, 1024 * 1024))
                    if not chunk:
                        raise _WheelsetError('ARTIFACT_CHANGED')
                    unread -= len(chunk)
                    digest.update(chunk)
                remaining -= size
                if not hmac.compare_digest(digest.hexdigest(), wheels[filename]):
                    result['mismatched_artifacts'].append(filename)
                elif prepare:
                    project, version = identities[filename]
                    dependencies[project] = _wheel_requirements(stream, project, version)
        if result['mismatched_artifacts']:
            result['blockers'].append('WHEEL_DIGEST_MISMATCH')
        else:
            if (not os.path.samestat(directory_info, _local_path(directory))
                    or _wheel_directory_entries(directory) != present
                    or any(_file_stamp(_local_path(directory / name)) != stamp
                           for name, stamp in verified.items())):
                raise _WheelsetError('WHEEL_DIRECTORY_CHANGED')
            if not prepare:
                result['status'] = 'WHEELSET_MATCHES_REVIEWED_LOCK'
            else:
                _verify_dependency_closure(pins, dependencies, result)
                if install_destination is None:
                    _write_install_requirements(install_requirements, directory, pins, approved_lock_sha256)
                    result.update(status='INSTALL_REQUIREMENTS_WRITTEN', dependency_resolution='REQUIRED')
                else:
                    installed = _install_disposable(install_destination, directory, wheels, pins, approved_lock_sha256)
                    result.update(status='DISPOSABLE_PACKAGES_INSTALLED', installed_distributions=installed,
                                  application_import='NOT_ATTEMPTED', listener_acceptance='NOT_ATTEMPTED')
    except _WheelsetError as exc:
        result['blockers'].append(str(exc))
    except (ValueError, RecursionError):
        result['blockers'].append('INVALID_WHEEL_LOCK_OR_PATH')
    except OSError:
        result['blockers'].append('LOCAL_WHEEL_ARTIFACT_UNAVAILABLE')
    except KeyboardInterrupt:
        result['blockers'].append('OPERATION_INTERRUPTED')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel-directory', type=Path)
    parser.add_argument('--lock', type=Path)
    parser.add_argument('--lock-sha256')
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument('--write-install-requirements', type=Path)
    operation.add_argument('--install-disposable-packages', type=Path, metavar='DESTINATION')
    args = parser.parse_args(argv)
    if (args.wheel_directory is None and args.lock is None and args.lock_sha256 is None
            and args.write_install_requirements is None and args.install_disposable_packages is None):
        result = inspect_runtime()
    elif args.wheel_directory is None or args.lock is None or args.lock_sha256 is None:
        result = {'status': 'BLOCKED', 'blockers': ['REVIEWED_WHEELSET_UNPROVISIONED']}
    else:
        result = verify_wheelset(args.wheel_directory, args.lock, approved_lock_sha256=args.lock_sha256,
                                 install_requirements=args.write_install_requirements,
                                 install_destination=args.install_disposable_packages)
    print(json.dumps(result, separators=(',', ':')))
    return 0 if result['status'] in ('WHEELSET_MATCHES_REVIEWED_LOCK', 'INSTALL_REQUIREMENTS_WRITTEN',
                                    'DISPOSABLE_PACKAGES_INSTALLED') else 2


if __name__ == '__main__':
    raise SystemExit(main())
