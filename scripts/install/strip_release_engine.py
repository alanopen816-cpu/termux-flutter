#!/usr/bin/env python3
"""Safely remove DWARF/static symbols from staged Termux release libflutter.so."""

from __future__ import annotations

import argparse
import fcntl
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


MIN_INPUT_BYTES = 64 * 1024 * 1024
MIN_SAVINGS_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
LOCK_TIMEOUT_SECONDS = 10.0
STRIP_TIMEOUT_SECONDS = 120.0
ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
PROGRAM_HEADER = struct.Struct("<IIQQQQQQ")
SECTION_HEADER = struct.Struct("<IIQQQQIIQQ")
PT_LOAD = 1
SHT_NOBITS = 8
SHF_ALLOC = 2
SHT_DYNAMIC = 6
SHT_DYNSYM = 11
MACHINE_AARCH64 = 183
ELF_ET_DYN = 3
PRESERVED_SECTION_PREFIXES = (
    ".dynamic", ".dynsym", ".dynstr", ".rela", ".rel", ".gnu.hash",
    ".hash", ".gnu.version", ".gnu.liblist",
)


class StripError(RuntimeError):
    """An unsafe strip request or invalid native output."""


@dataclass(frozen=True)
class Section:
    name: str
    kind: int
    flags: int
    address: int
    offset: int
    size: int
    link: int
    info: int
    alignment: int
    entry_size: int


@dataclass(frozen=True)
class Elf:
    header: tuple
    programs: tuple[tuple, ...]
    load_contents: tuple[bytes, ...]
    sections: tuple[Section, ...]
    contents: tuple[bytes | None, ...]
    file_size: int


def _read_exact(stream, size: int, offset: int) -> bytes:
    if size < 0 or offset < 0:
        raise StripError("ELF contains a negative range")
    stream.seek(offset)
    data = stream.read(size)
    if len(data) != size:
        raise StripError("ELF contains a truncated table or segment")
    return data


def inspect_elf(path: Path) -> Elf:
    """Parse enough ELF64 metadata to compare executable/runtime contracts."""
    path = Path(path)
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            raw = _read_exact(stream, ELF_HEADER.size, 0)
            header = ELF_HEADER.unpack(raw)
            ident, elf_type, machine, version, entry, phoff, shoff, flags, ehsize, phentsize, phnum, shentsize, shnum, shstrndx = header
            if ident[:7] != b"\x7fELF\x02\x01\x01" or elf_type != ELF_ET_DYN or machine != MACHINE_AARCH64:
                raise StripError("ELF must be 64-bit little-endian AArch64 ET_DYN")
            if version != 1 or ehsize != ELF_HEADER.size or phentsize != PROGRAM_HEADER.size or shentsize != SECTION_HEADER.size:
                raise StripError("ELF header uses an unsupported format")
            if not phnum or phnum > 4096 or not shnum or shnum > 65535 or shstrndx >= shnum:
                raise StripError("ELF table counts are invalid")
            if phoff + phnum * phentsize > size or shoff + shnum * shentsize > size:
                raise StripError("ELF table extends beyond file")
            programs = tuple(PROGRAM_HEADER.unpack(_read_exact(stream, phentsize, phoff + i * phentsize)) for i in range(phnum))
            load_contents = []
            for program in programs:
                if program[0] == PT_LOAD:
                    if program[5] > program[6] or program[2] + program[5] > size:
                        raise StripError("ELF loadable segment has an invalid range")
                    load_contents.append(_read_exact(stream, program[5], program[2]))
            raw_sections = [SECTION_HEADER.unpack(_read_exact(stream, shentsize, shoff + i * shentsize)) for i in range(shnum)]
            names_raw = raw_sections[shstrndx]
            if names_raw[1] != 3 or names_raw[4] + names_raw[5] > size:
                raise StripError("ELF section-name table is invalid")
            names = _read_exact(stream, names_raw[5], names_raw[4])

            def section_name(offset: int) -> str:
                if offset >= len(names):
                    raise StripError("ELF section name offset is invalid")
                end = names.find(b"\0", offset)
                if end < 0:
                    raise StripError("ELF section name is unterminated")
                return names[offset:end].decode("ascii", "strict")

            sections, contents = [], []
            for raw_section in raw_sections:
                name_offset, kind, sec_flags, address, offset, sec_size, link, info, alignment, entry_size = raw_section
                if kind != SHT_NOBITS and offset + sec_size > size:
                    raise StripError("ELF section extends beyond file")
                name = section_name(name_offset)
                section = Section(name, kind, sec_flags, address, offset, sec_size, link, info, alignment, entry_size)
                sections.append(section)
                contents.append(None if kind == SHT_NOBITS else _read_exact(stream, sec_size, offset))
            if not any(program[0] == PT_LOAD for program in programs):
                raise StripError("ELF has no loadable segments")
            return Elf(header, programs, tuple(load_contents), tuple(sections), tuple(contents), size)
    except (OSError, UnicodeError, struct.error) as exc:
        raise StripError(f"cannot inspect ELF: {exc}") from exc


def validate_target(path: Path, variant: str) -> Path:
    """Allow only the exact ARM64 shared-library slot in AGP release intermediates."""
    path = Path(path)
    if path.is_symlink():
        raise StripError("staged engine library must not be a symlink")
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise StripError(f"staged engine library is unavailable: {exc}") from exc
    parts = path.parts
    if variant.lower() != "release" or path.name != "libflutter.so" or "debug" in parts or "profile" in parts:
        raise StripError("only the release libflutter.so may be stripped")
    try:
        intermediate = parts.index("intermediates")
    except ValueError as exc:
        raise StripError("target is not under an AGP intermediates directory") from exc
    if (len(parts) < intermediate + 7 or parts[intermediate + 1] != "stripped_native_libs"
            or parts[intermediate + 2] != "release" or parts[-2:] != ("arm64-v8a", "libflutter.so")):
        raise StripError("target is outside the staged AGP release ARM64 libflutter.so slot")
    if not path.is_file():
        raise StripError("staged engine library must be a regular file")
    return path


def require_termux() -> None:
    if os.uname().sysname != "Linux" or not os.environ.get("TERMUX_VERSION"):
        raise StripError("release engine stripping is restricted to Linux Termux")


def validate_strip_tool(path: Path) -> Path:
    path = Path(path).absolute()
    if not path.exists():
        raise StripError("native llvm-strip is missing")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
        raise StripError("native llvm-strip must be an executable regular file")
    inspect_elf(path)
    return path


def _contract(elf: Elf) -> tuple:
    header = elf.header
    # Section-table location/count may change, but executable ELF identity and ABI may not.
    stable_header = (header[0], header[1], header[2], header[3], header[4], header[5],
                     header[7], header[8], header[9], header[10], header[11])
    loads = []
    load_contents = []
    load_index = 0
    for program in elf.programs:
        if program[0] == PT_LOAD:
            loads.append(program)
            payload = bytearray(elf.load_contents[load_index])
            load_index += 1
            # llvm-strip relocates section headers and updates only ELF section-table
            # bookkeeping in the first LOAD segment; those four fields are non-runtime.
            if program[2] <= 40 and program[2] + program[5] >= 64:
                for start, end in ((40, 48), (60, 62), (62, 64)):
                    payload[start - program[2]:end - program[2]] = bytes(end - start)
            load_contents.append(bytes(payload))
    protected = []
    for section, content in zip(elf.sections, elf.contents):
        if section.kind == SHT_NOBITS:
            data = None
        elif section.flags & SHF_ALLOC or section.kind in (SHT_DYNAMIC, SHT_DYNSYM) or section.name.startswith(PRESERVED_SECTION_PREFIXES):
            data = content
        else:
            continue
        protected.append((section.name, section.kind, section.flags, section.address, section.size,
                          section.link, section.info, section.alignment, section.entry_size, data))
    return stable_header, tuple(loads), tuple(load_contents), tuple(protected)


def _has_debug_or_static_symbols(elf: Elf) -> bool:
    for section in elf.sections:
        if section.name.startswith(".debug_") or section.name in (".symtab", ".strtab"):
            if section.kind != SHT_NOBITS and section.size:
                return True
    return False


def validate_engine_metadata(elf: Elf) -> None:
    """Require a usable JNI shared object before considering strip or idempotence."""
    by_name = {}
    for index, section in enumerate(elf.sections):
        if section.name in (".dynamic", ".dynsym", ".dynstr"):
            if section.name in by_name:
                raise StripError(f"ELF contains duplicate {section.name} sections")
            by_name[section.name] = (index, section, elf.contents[index])
    if set(by_name) != {".dynamic", ".dynsym", ".dynstr"}:
        raise StripError("ELF is missing dynamic linker sections")
    dynamic_index, dynamic, dynamic_data = by_name[".dynamic"]
    dynstr_index, dynstr, strings = by_name[".dynstr"]
    _, dynsym, symbols = by_name[".dynsym"]
    if (dynamic.kind != SHT_DYNAMIC or dynamic.link != dynstr_index
            or dynsym.kind != SHT_DYNSYM or dynsym.link != dynstr_index
            or dynamic_data is None or strings is None or symbols is None
            or dynamic.entry_size != 16 or dynamic.size % 16
            or dynsym.entry_size != 24 or dynsym.size % 24):
        raise StripError("ELF dynamic linker metadata is malformed")

    def string_at(offset: int) -> bytes:
        if offset < 0 or offset >= len(strings):
            raise StripError("ELF dynamic string offset is invalid")
        end = strings.find(b"\0", offset)
        if end < 0:
            raise StripError("ELF dynamic string is unterminated")
        return strings[offset:end]

    tags = {}
    terminated = False
    for offset in range(0, len(dynamic_data), 16):
        tag, value = struct.unpack_from("<qQ", dynamic_data, offset)
        if tag == 0:
            terminated = True
            break
        tags.setdefault(tag, []).append(value)
    if (not terminated or not tags.get(1) or not tags.get(14)
            or tags.get(5) != [dynstr.address] or tags.get(10) != [dynstr.size]
            or tags.get(6) != [dynsym.address] or tags.get(11) != [dynsym.entry_size]):
        raise StripError("ELF dynamic dependencies or symbol-table links are invalid")
    for value in tags[1] + tags[14]:
        string_at(value)

    exports = set()
    for offset in range(0, len(symbols), 24):
        name_offset, info, other, section_index, _value, _size = struct.unpack_from("<IBBHQQ", symbols, offset)
        if section_index and info >> 4 in (1, 2) and other & 0x3 in (0, 3) and name_offset:
            exports.add(string_at(name_offset))
    if b"JNI_OnLoad" not in exports:
        raise StripError("ELF does not export JNI_OnLoad")


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fingerprint(path: Path) -> tuple:
    info = Path(path).stat()
    return (info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode,
            info.st_uid, info.st_gid, _sha256(path))


def _acquire_directory_lock(directory_fd: int) -> None:
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StripError("timed out waiting for the staged library lock deadline") from exc
            time.sleep(min(0.05, remaining))


def strip_release(path: Path, tool: Path, *, variant: str = "release", output_path: Path | None = None) -> bool:
    """Strip a validated candidate in a same-filesystem copy; publish only after proof."""
    require_termux()
    source_path = validate_target(path, variant)
    path = source_path if output_path is None else Path(output_path)
    if output_path is not None:
        if path.is_symlink() or not path.is_file():
            raise StripError("isolated strip output must be an existing regular file")
        if path.resolve() == source_path or path.name != "libflutter.so" or path.parent.name != "arm64-v8a":
            raise StripError("isolated strip output is not the copied release ARM64 library")
    tool = validate_strip_tool(tool)
    if path.parent.stat().st_dev != path.stat().st_dev:
        raise StripError("staged library and its parent are on different filesystems")
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    temporary = None
    try:
        _acquire_directory_lock(directory_fd)
        source_fingerprint = _fingerprint(source_path)
        destination_fingerprint = _fingerprint(path)
        before_info = path.stat()
        original = inspect_elf(path)
        validate_engine_metadata(original)
        if output_path is not None and source_fingerprint[-1] != destination_fingerprint[-1]:
            raise StripError("isolated output differs from the AGP staged input")
        if before_info.st_size <= MIN_INPUT_BYTES and not _has_debug_or_static_symbols(original):
            if before_info.st_size >= MAX_OUTPUT_BYTES:
                raise StripError("already-stripped candidate exceeds the output size bound")
            return False
        if before_info.st_size <= MIN_INPUT_BYTES:
            raise StripError(f"staged release library is not an unstripped candidate ({before_info.st_size} bytes)")
        if not _has_debug_or_static_symbols(original):
            raise StripError("large release library has no removable DWARF/static symbol sections")
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.termux-strip-", dir=path.parent)
        temporary = Path(temp_name)
        os.close(descriptor)
        shutil.copyfile(path, temporary)
        subprocess.run([str(tool), "--strip-unneeded", "-o", str(temporary), str(path)],
                       check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=STRIP_TIMEOUT_SECONDS)
        candidate = inspect_elf(temporary)
        validate_engine_metadata(candidate)
        if _contract(original) != _contract(candidate):
            raise StripError("strip changed ELF loadable or runtime metadata")
        if _has_debug_or_static_symbols(candidate):
            raise StripError("strip output still contains DWARF/static symbols")
        saved = before_info.st_size - candidate.file_size
        if saved < MIN_SAVINGS_BYTES or candidate.file_size >= MAX_OUTPUT_BYTES:
            raise StripError("stripped output did not meet the size safety bounds")
        shutil.copystat(path, temporary, follow_symlinks=False)
        os.chown(temporary, before_info.st_uid, before_info.st_gid)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        if (_fingerprint(source_path) != source_fingerprint
                or _fingerprint(path) != destination_fingerprint):
            raise StripError("staged library changed during stripping; refusing to publish stale output")
        os.replace(temporary, path)
        temporary = None
    except subprocess.TimeoutExpired as exc:
        raise StripError(f"native llvm-strip timed out after {exc.timeout} seconds") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", "replace")[-500:]
        raise StripError(f"native llvm-strip failed: {message}") from exc
    except OSError as exc:
        raise StripError(f"could not safely publish stripped library: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        os.close(directory_fd)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--llvm-strip", type=Path, required=True)
    parser.add_argument("--output-library", type=Path)
    args = parser.parse_args(argv)
    try:
        strip_release(args.library, args.llvm_strip, variant=args.variant, output_path=args.output_library)
    except StripError as exc:
        print(f"release engine strip rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
