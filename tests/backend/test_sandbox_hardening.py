"""
tests/backend/test_sandbox_hardening.py
----------------------------------------
Phase 7 tests for Sandbox Hardening, Path Security, and Atomic File Operations.

Tests:
- Rejection of Windows UNC network paths
- Rejection of Windows drive-letter paths
- Rejection of Windows reserved device names (CON, PRN, NUL, COM1-9, LPT1-9)
- Rejection of directory traversal (..)
- Rejection of symlink escapes
- Atomic file write and overwrite protection
"""

import os
import pytest
from pathlib import Path

from backend.tools.safety import atomic_write_file, validate_path_within


class TestSandboxPathHardening:
    """Test rigorous sandbox path validation rules."""

    @pytest.fixture
    def sandbox(self, tmp_path: Path):
        sb = tmp_path / "sandbox"
        sb.mkdir(parents=True, exist_ok=True)
        return sb

    def test_valid_relative_path(self, sandbox):
        p = validate_path_within("sub/file.txt", sandbox)
        assert p == (sandbox / "sub" / "file.txt").resolve()

    def test_reject_null_bytes(self, sandbox):
        with pytest.raises(ValueError, match="null bytes"):
            validate_path_within("file\x00.txt", sandbox)

    def test_reject_unc_paths(self, sandbox):
        with pytest.raises(ValueError, match="UNC network paths"):
            validate_path_within(r"\\192.168.1.1\share\payload.txt", sandbox)
        with pytest.raises(ValueError, match="UNC network paths"):
            validate_path_within("//server/share/payload.txt", sandbox)

    def test_reject_drive_letters(self, sandbox):
        with pytest.raises(ValueError, match="drive-letter paths"):
            validate_path_within("C:/Windows/System32/calc.exe", sandbox)
        with pytest.raises(ValueError, match="drive-letter paths"):
            validate_path_within("D:\\data\\file.txt", sandbox)

    def test_reject_traversal_components(self, sandbox):
        with pytest.raises(ValueError, match="Path traversal"):
            validate_path_within("../outside.txt", sandbox)
        with pytest.raises(ValueError, match="Path traversal"):
            validate_path_within("sub/../../outside.txt", sandbox)

    @pytest.mark.parametrize("reserved", ["con", "CON.txt", "prn", "AUX.log", "nul", "COM1", "com9.dat", "lpt1"])
    def test_reject_reserved_device_names(self, sandbox, reserved):
        with pytest.raises(ValueError, match="Reserved device name"):
            validate_path_within(reserved, sandbox)
        with pytest.raises(ValueError, match="Reserved device name"):
            validate_path_within(f"sub/{reserved}", sandbox)

    def test_reject_symlink_escapes(self, sandbox, tmp_path):
        # Create outside target
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir(parents=True, exist_ok=True)
        secret_file = outside_dir / "secret.txt"
        secret_file.write_text("classified", encoding="utf-8")

        # Create symlink inside sandbox pointing outside
        symlink_dir = sandbox / "symlink_dir"
        try:
            os.symlink(outside_dir, symlink_dir, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("Symlink creation requires elevated privileges on this environment")

        with pytest.raises(ValueError, match="Symlinks are not allowed"):
            validate_path_within("symlink_dir/secret.txt", sandbox)


class TestAtomicFileOperations:
    """Test atomic file write and overwrite enforcement."""

    @pytest.fixture
    def target_dir(self, tmp_path: Path):
        d = tmp_path / "write_test"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_atomic_write_new_file(self, target_dir):
        target = target_dir / "output.txt"
        written = atomic_write_file(target, "Hello Sovereign World!", overwrite=False)
        assert written == 22
        assert target.read_text(encoding="utf-8") == "Hello Sovereign World!"

    def test_atomic_write_overwrite_prevention(self, target_dir):
        target = target_dir / "existing.txt"
        target.write_text("Initial content", encoding="utf-8")

        with pytest.raises(ValueError, match="already exists"):
            atomic_write_file(target, "New Content", overwrite=False)

        assert target.read_text(encoding="utf-8") == "Initial content"

    def test_atomic_write_with_overwrite_allowed(self, target_dir):
        target = target_dir / "overwrite_me.txt"
        target.write_text("Old content", encoding="utf-8")

        atomic_write_file(target, "Overwritten successfully!", overwrite=True)
        assert target.read_text(encoding="utf-8") == "Overwritten successfully!"
