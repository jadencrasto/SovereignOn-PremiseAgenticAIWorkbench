"""Operator-only diagnostic proposal. Do not run without an approved isolated environment."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys

CHILD = """import asyncio,faulthandler,sys
print('interpreter=' + sys.executable, flush=True)
print('version=' + sys.version.replace('\\n', ' '), flush=True)
faulthandler.dump_traceback_later(3, repeat=False)
print('before event loop', flush=True)
asyncio.run(asyncio.sleep(0))
print('event loop completed', flush=True)
faulthandler.cancel_dump_traceback_later()
"""


def main():
    parser = argparse.ArgumentParser(description='Runs one loopback socketpair diagnostic with a parent timeout.')
    parser.add_argument('--interpreter', required=True, help='Absolute path to the approved guest interpreter')
    parser.add_argument('--approved-isolated', action='store_true', help='Operator acknowledgement, not proof of isolation')
    args = parser.parse_args()
    exe = Path(args.interpreter)
    if not args.approved_isolated or not exe.is_absolute() or not exe.is_file():
        parser.error('Requires an absolute interpreter path and operator acknowledgement')
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    print('interpreter_sha256=' + digest, flush=True)
    try:
        result = subprocess.run([str(exe), '-c', CHILD], capture_output=True, text=True, timeout=8, check=False)
    except subprocess.TimeoutExpired as exc:
        print('HARD_TIMEOUT: child terminated after 8 seconds', flush=True)
        for stream in (exc.stdout, exc.stderr):
            if stream:
                print(stream.decode(errors='replace') if isinstance(stream, bytes) else stream, flush=True)
        return 124
    print(result.stdout, end='')
    print(result.stderr, end='', file=sys.stderr)
    print('child_exit_code=' + str(result.returncode))
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
