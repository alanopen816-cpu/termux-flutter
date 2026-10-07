import importlib.util
import struct
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/install/strip_release_engine.py"
SPEC = importlib.util.spec_from_file_location("strip_release_engine", SCRIPT)
stripper = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stripper
SPEC.loader.exec_module(stripper)


def _small_valid_elf(path, *, machine=183):
    names = b"\0.text\0.shstrtab\0"
    text_offset = 64 + 56
    names_offset = text_offset + 4
    section_offset = names_offset + len(names)
    ident = b"\x7fELF" + bytes((2, 1, 1, 0)) + bytes(8)
    header = struct.pack("<16sHHIQQQIHHHHHH", ident, 3, machine, 1, 0x1000,
                         64, section_offset, 0, 64, 56, 1, 64, 3, 2)
    program = struct.pack("<IIQQQQQQ", 1, 5, text_offset, 0x1000, 0x1000, 4, 4, 4096)
    null_section = bytes(64)
    text_section = struct.pack("<IIQQQQIIQQ", 1, 1, 2, 0x1000, text_offset, 4, 0, 0, 4, 0)
    names_section = struct.pack("<IIQQQQIIQQ", 7, 3, 0, 0, names_offset, len(names), 0, 0, 1, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + program + b"TEXT" + names + null_section + text_section + names_section)
    return path


def test_inspection_rejects_wrong_elf_architecture(tmp_path):
    path = _small_valid_elf(tmp_path / "libflutter.so", machine=62)
    with pytest.raises(stripper.StripError, match="AArch64"):
        stripper.inspect_elf(path)


def test_inspection_records_aarch64_loadable_bytes(tmp_path):
    elf = stripper.inspect_elf(_small_valid_elf(tmp_path / "libflutter.so"))

    assert elf.header[2] == 183
    assert elf.load_contents == (b"TEXT",)


def test_scope_accepts_only_release_arm64_flutter_library(tmp_path):
    accepted = tmp_path / "build/intermediates/stripped_native_libs/release/out/lib/arm64-v8a/libflutter.so"
    accepted.parent.mkdir(parents=True)
    accepted.touch()
    assert stripper.validate_target(accepted, "release") == accepted.resolve()
    flavored = tmp_path / "build/intermediates/stripped_native_libs/release/stripFreeReleaseDebugSymbols/out/lib/arm64-v8a/libflutter.so"
    flavored.parent.mkdir(parents=True)
    flavored.touch()
    assert stripper.validate_target(flavored, "release") == flavored.resolve()
    for variant, relative in [
        ("debug", accepted.relative_to(tmp_path / "build")),
        ("profile", accepted.relative_to(tmp_path / "build")),
        ("release", Path("build/intermediates/stripped_native_libs/release/out/lib/x86_64/libflutter.so")),
        ("release", Path("build/intermediates/merged_native_libs/release/out/lib/arm64-v8a/libapp.so")),
    ]:
        if not (tmp_path / relative).exists():
            (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / relative).touch()
        with pytest.raises(stripper.StripError):
            stripper.validate_target(tmp_path / relative, variant)


def test_non_termux_host_fails_closed_before_running_strip(tmp_path, monkeypatch):
    monkeypatch.setattr(stripper.os, "uname", lambda: type("Uname", (), {"sysname": "Linux"})())
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    with pytest.raises(stripper.StripError, match="Termux"):
        stripper.require_termux()


def test_wrong_native_strip_architecture_is_rejected(tmp_path):
    tool = _small_valid_elf(tmp_path / "llvm-strip", machine=62)
    tool.chmod(0o755)
    with pytest.raises(stripper.StripError, match="AArch64"):
        stripper.validate_strip_tool(tool)


def test_strip_tool_uses_native_symlink_name_after_validating_target(tmp_path, monkeypatch):
    target = _small_valid_elf(tmp_path / "llvm-objcopy")
    target.chmod(0o755)
    alias = tmp_path / "llvm-strip"
    alias.symlink_to(target)
    seen = []

    def inspect(path):
        seen.append(Path(path))
        assert Path(path).resolve() == target.resolve()

    monkeypatch.setattr(stripper, "inspect_elf", inspect)

    assert stripper.validate_strip_tool(alias) == alias.absolute()
    assert seen == [alias.absolute()]


def test_invalid_staged_elf_preserves_original_bytes_and_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(stripper.os, "uname", lambda: type("Uname", (), {"sysname": "Linux"})())
    monkeypatch.setenv("TERMUX_VERSION", "test")
    target = tmp_path / "build/intermediates/stripped_native_libs/release/out/lib/arm64-v8a/libflutter.so"
    _small_valid_elf(target, machine=62)
    target.chmod(0o640)
    tool = _small_valid_elf(tmp_path / "llvm-strip")
    tool.chmod(0o755)
    before = (stripper._sha256(target), target.stat().st_ino, target.stat().st_mode)

    with pytest.raises(stripper.StripError, match="AArch64"):
        stripper.strip_release(target, tool)

    assert (stripper._sha256(target), target.stat().st_ino, target.stat().st_mode) == before


def _mock_strip_preflight(tmp_path, monkeypatch):
    monkeypatch.setattr(stripper.os, "uname", lambda: SimpleNamespace(sysname="Linux"))
    monkeypatch.setenv("TERMUX_VERSION", "test")
    monkeypatch.setattr(stripper, "MIN_INPUT_BYTES", 1024 * 1024)
    target = tmp_path / "build/intermediates/stripped_native_libs/release/out/lib/arm64-v8a/libflutter.so"
    target.parent.mkdir(parents=True)
    with target.open("wb") as stream:
        stream.truncate(2 * 1024 * 1024)
    target.chmod(0o640)
    tool = tmp_path / "llvm-strip"
    tool.write_bytes(b"native tool fixture")
    tool.chmod(0o755)
    monkeypatch.setattr(
        stripper, "inspect_elf",
        lambda path: SimpleNamespace(file_size=Path(path).stat().st_size),
    )
    monkeypatch.setattr(stripper, "validate_engine_metadata", lambda _elf: None)
    monkeypatch.setattr(stripper, "_contract", lambda _elf: "same runtime contract")
    monkeypatch.setattr(
        stripper, "_has_debug_or_static_symbols",
        lambda elf: elf.file_size > stripper.MIN_INPUT_BYTES,
    )
    return target, tool


def test_strip_process_has_a_deadline_and_timeout_preserves_input(tmp_path, monkeypatch):
    target, tool = _mock_strip_preflight(tmp_path, monkeypatch)
    before = (stripper._sha256(target), target.stat().st_ino, target.stat().st_mode)

    def timed_out(_command, **kwargs):
        assert kwargs.get("timeout") == stripper.STRIP_TIMEOUT_SECONDS
        raise __import__("subprocess").TimeoutExpired("llvm-strip", kwargs["timeout"])

    monkeypatch.setattr(stripper.subprocess, "run", timed_out)
    with pytest.raises(stripper.StripError, match="timed out"):
        stripper.strip_release(target, tool)

    assert (stripper._sha256(target), target.stat().st_ino, target.stat().st_mode) == before


def test_stale_in_place_mutation_is_not_overwritten(tmp_path, monkeypatch):
    target, tool = _mock_strip_preflight(tmp_path, monkeypatch)

    def mutate_source(_command, **kwargs):
        output = Path(_command[_command.index("-o") + 1])
        output.write_bytes(b"stripped")
        source = Path(_command[-1])
        source.write_bytes(b"concurrent writer changed source")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(stripper.subprocess, "run", mutate_source)
    with pytest.raises(stripper.StripError, match="changed during stripping"):
        stripper.strip_release(target, tool)

    assert target.read_bytes() == b"concurrent writer changed source"


def test_strip_lock_wait_is_bounded_and_preserves_input(tmp_path, monkeypatch):
    target, tool = _mock_strip_preflight(tmp_path, monkeypatch)
    monkeypatch.setattr(stripper, "LOCK_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(stripper.fcntl, "flock", lambda *_args: (_ for _ in ()).throw(BlockingIOError()))
    before = stripper._sha256(target)

    with pytest.raises(stripper.StripError, match="lock deadline"):
        stripper.strip_release(target, tool)

    assert stripper._sha256(target) == before
