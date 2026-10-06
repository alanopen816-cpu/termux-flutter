import struct
import io
import tarfile
from pathlib import Path

import pytest

import build
from build import REQUIRED_DEB_ARTIFACTS, validate_android_gen_snapshot


def _write_elf(path, *, machine=183, elf_type=3, interpreter="/system/bin/linker64", executable=True):
    """Write the ELF64 little-endian header and PT_INTERP used by our tests."""
    interpreter_bytes = interpreter.encode() + b"\0"
    header_size = 64
    program_header_size = 56
    interpreter_offset = header_size + program_header_size
    ident = b"\x7fELF" + bytes((2, 1, 1, 0)) + bytes(8)
    header = struct.pack(
        "<16sHHIQQQIHHHHHH",
        ident, elf_type, machine, 1, 0, header_size, 0, 0,
        header_size, program_header_size, 1, 0, 0, 0,
    )
    program_header = struct.pack(
        "<IIQQQQQQ",
        3, 4, interpreter_offset, 0, 0,
        len(interpreter_bytes), len(interpreter_bytes), 1,
    )
    path.write_bytes(header + program_header + interpreter_bytes)
    path.chmod(0o755 if executable else 0o644)
    return path


def test_validate_android_gen_snapshot_accepts_arm64_termux_elf(tmp_path):
    path = _write_elf(tmp_path / "gen_snapshot")

    assert validate_android_gen_snapshot(path) is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"machine": 62}, "AArch64"),
        ({"interpreter": "/lib64/ld-linux-x86-64.so.2"}, "interpreter"),
        ({"interpreter": "/system/bin/linker"}, "interpreter"),
        ({"executable": False}, "executable"),
    ],
)
def test_validate_android_gen_snapshot_rejects_invalid_host_binary(tmp_path, kwargs, message):
    path = _write_elf(tmp_path / "gen_snapshot", **kwargs)

    with pytest.raises((ValueError, RuntimeError), match=message):
        validate_android_gen_snapshot(path)


def test_validate_android_gen_snapshot_rejects_truncated_or_non_elf(tmp_path):
    path = tmp_path / "gen_snapshot"
    path.write_bytes(b"not an executable")
    path.chmod(0o755)

    with pytest.raises((ValueError, RuntimeError), match="ELF"):
        validate_android_gen_snapshot(path)


def test_validate_android_gen_snapshot_rejects_missing_file(tmp_path):
    with pytest.raises((ValueError, RuntimeError), match="not found"):
        validate_android_gen_snapshot(tmp_path / "missing")



def test_required_deb_artifacts_include_release_and_profile_generators():
    assert any("android-arm64-release" in path and path.endswith("/gen_snapshot")
               for path in REQUIRED_DEB_ARTIFACTS)


def _stub_deb(monkeypatch, tmp_path, *, missing=None, invalid=None, non_executable=None):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as data_tar:
        for suffix in REQUIRED_DEB_ARTIFACTS:
            if suffix == missing:
                continue
            data = b"runtime" if not suffix.endswith("/gen_snapshot") else b""
            if suffix.endswith("/gen_snapshot"):
                options = invalid[1] if invalid and invalid[0] == suffix else {}
                source = _write_elf(tmp_path / Path(suffix).parent.name, **options)
                data = source.read_bytes()
            member = tarfile.TarInfo(f"data/data/com.termux/files/usr/{suffix}")
            member.mode = 0o644 if suffix == non_executable else 0o755
            member.size = len(data)
            data_tar.addfile(member, io.BytesIO(data))
    monkeypatch.setattr(build, "_ar_members", lambda _path: iter([("data.tar", archive.getvalue())]))
    return tmp_path / "fixture.deb"


def test_validate_deb_artifacts_accepts_both_arm64_generators(tmp_path, monkeypatch):
    build.validate_deb_artifacts(_stub_deb(monkeypatch, tmp_path))


def test_validate_deb_artifacts_requires_profile_generator(tmp_path, monkeypatch):
    profile = next(path for path in REQUIRED_DEB_ARTIFACTS if "android-arm64-profile" in path)
    with pytest.raises(RuntimeError, match="missing required"):
        build.validate_deb_artifacts(_stub_deb(monkeypatch, tmp_path, missing=profile))


@pytest.mark.parametrize(
    ("invalid", "non_executable"),
    [
        ((next(path for path in REQUIRED_DEB_ARTIFACTS if "android-arm64-release" in path), {"machine": 62}), None),
        ((next(path for path in REQUIRED_DEB_ARTIFACTS if "android-arm64-profile" in path), {"interpreter": "/lib64/ld-linux-x86-64.so.2"}), None),
        (None, next(path for path in REQUIRED_DEB_ARTIFACTS if "android-arm64-release" in path)),
    ],
)
def test_validate_deb_artifacts_rejects_invalid_generator_modes(
    tmp_path, monkeypatch, invalid, non_executable
):
    deb = _stub_deb(monkeypatch, tmp_path, invalid=invalid, non_executable=non_executable)
    with pytest.raises((RuntimeError, ValueError)):
        build.validate_deb_artifacts(deb)
    assert any("android-arm64-profile" in path and path.endswith("/gen_snapshot")
               for path in REQUIRED_DEB_ARTIFACTS)
