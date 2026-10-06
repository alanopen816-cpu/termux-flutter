#!/usr/bin/env python3
"""Safely configure Android paths and Gradle's native Termux AAPT2 override."""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import subprocess
import tempfile
from pathlib import Path


class ConfigurationError(RuntimeError):
    """An unsafe or invalid Android configuration prevented an update."""


def is_termux_runtime(prefix: Path) -> bool:
    """Require Android system identity and Termux's package context, not path names."""
    try:
        result = subprocess.run(
            ["/system/bin/getprop", "ro.build.version.sdk"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return (
        result.returncode == 0
        and result.stdout.strip().isdigit()
        and Path("/system/bin/linker64").is_file()
        and os.access(prefix / "bin/pkg", os.X_OK)
    )


def is_native_termux_aapt2(path: Path) -> bool:
    """Validate an executable ELF64 AArch64 binary using Android's linker64."""
    try:
        if not path.is_file() or not os.access(path, os.X_OK):
            return False
        data = path.read_bytes()
        if len(data) < 64 or data[:7] != b"\x7fELF\x02\x01\x01":
            return False
        if struct.unpack_from("<H", data, 18)[0] != 183:
            return False
        phoff = struct.unpack_from("<Q", data, 32)[0]
        phentsize, phnum = struct.unpack_from("<HH", data, 54)
        if phentsize < 56 or phoff + phentsize * phnum > len(data):
            return False
        for index in range(phnum):
            start = phoff + index * phentsize
            p_type = struct.unpack_from("<I", data, start)[0]
            if p_type != 3:  # PT_INTERP
                continue
            p_offset = struct.unpack_from("<Q", data, start + 8)[0]
            p_filesz = struct.unpack_from("<Q", data, start + 32)[0]
            if p_offset + p_filesz > len(data):
                return False
            return data[p_offset : p_offset + p_filesz].rstrip(b"\0") == b"/system/bin/linker64"
    except (OSError, ValueError, struct.error):
        return False
    return False


def _read_json(path: Path) -> tuple[dict, os.stat_result | None]:
    if path.is_symlink():
        raise ConfigurationError(f"refusing symlink configuration file: {path}")
    if not path.exists():
        return {}, None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"expected a JSON object in {path}")
    return value, path.stat()


def _read_properties(path: Path) -> tuple[str, os.stat_result | None, str | None]:
    if path.is_symlink():
        raise ConfigurationError(f"refusing symlink configuration file: {path}")
    if not path.exists():
        return "", None, None
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError(f"cannot read {path}: {exc}") from exc
    matches = []
    for line in _logical_property_lines(text):
        parsed = _parse_java_property(line)
        if parsed is not None and parsed[0] == "android.aapt2FromMavenOverride":
            matches.append(parsed[1])
    return text, path.stat(), matches[-1] if matches else None


def _logical_property_lines(text: str) -> list[str]:
    """Join Java-properties continuation lines without changing stored bytes."""
    logical = []
    current = ""
    continuing = False
    for physical in re.split(r"\r\n|\r|\n", text):
        line = physical.lstrip(" \t\f") if continuing else physical
        current += line
        trailing_slashes = len(current) - len(current.rstrip("\\"))
        if trailing_slashes % 2:
            current = current[:-1]
            continuing = True
            continue
        logical.append(current)
        current = ""
        continuing = False
    if continuing:
        raise ConfigurationError("invalid Java properties continuation at end of file")
    if current:
        logical.append(current)
    return logical


def _decode_java_property_escapes(value: str) -> str:
    decoded = []
    index = 0
    translations = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}
    while index < len(value):
        character = value[index]
        index += 1
        if character != "\\":
            decoded.append(character)
            continue
        if index == len(value):
            raise ConfigurationError("invalid trailing escape in Java properties")
        escaped = value[index]
        index += 1
        if escaped == "u":
            digits = value[index : index + 4]
            if len(digits) != 4 or not re.fullmatch(r"[0-9a-fA-F]{4}", digits):
                raise ConfigurationError("invalid Unicode escape in Java properties")
            decoded.append(chr(int(digits, 16)))
            index += 4
        else:
            decoded.append(translations.get(escaped, escaped))
    return "".join(decoded)


def _parse_java_property(line: str) -> tuple[str, str] | None:
    """Parse one logical java.util.Properties line, including escaped keys."""
    content = line.lstrip(" \t\f")
    if not content or content[0] in "#!":
        return None
    escaped = False
    delimiter = len(content)
    separator = None
    for index, character in enumerate(content):
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character in "=: \t\f":
            delimiter = index
            separator = character
            break
    raw_key = content[:delimiter]
    if separator is None:
        raw_value = ""
    elif separator in " \t\f":
        value_start = delimiter
        while value_start < len(content) and content[value_start] in " \t\f":
            value_start += 1
        if value_start < len(content) and content[value_start] in "=:":
            value_start += 1
        while value_start < len(content) and content[value_start] in " \t\f":
            value_start += 1
        raw_value = content[value_start:]
    else:
        value_start = delimiter + 1
        while value_start < len(content) and content[value_start] in " \t\f":
            value_start += 1
        raw_value = content[value_start:]
    return _decode_java_property_escapes(raw_key), _decode_java_property_escapes(raw_value)


def _escape_java_property_value(value: str) -> str:
    escaped = []
    for index, character in enumerate(value):
        if character == "\\":
            escaped.append("\\\\")
        elif character == " ":
            escaped.append("\\ ")
        elif character == "\t":
            escaped.append("\\t")
        elif character == "\f":
            escaped.append("\\f")
        elif character in ":=#!":
            escaped.append("\\" + character)
        elif character in "\r\n":
            raise ConfigurationError("AAPT2 path contains a newline")
        else:
            escaped.append(character)
    return "".join(escaped)


def _atomic_write(path: Path, data: bytes, previous: os.stat_result | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        mode = (previous.st_mode & 0o7777) if previous else 0o600
        if previous is not None:
            try:
                os.chown(temporary, previous.st_uid, previous.st_gid)
            except PermissionError as exc:
                temporary_stat = os.stat(temporary)
                if (temporary_stat.st_uid, temporary_stat.st_gid) != (previous.st_uid, previous.st_gid):
                    raise PermissionError(f"unable to preserve ownership for {path}") from exc
        if previous is not None:
            temporary_stat = os.stat(temporary)
            if (temporary_stat.st_uid, temporary_stat.st_gid) != (previous.st_uid, previous.st_gid):
                raise PermissionError(f"unable to preserve ownership for {path}")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def configure(prefix: Path, home: Path, android_sdk: Path, gradle_user_home: Path | None = None) -> bool:
    prefix, home, android_sdk = map(Path, (prefix, home, android_sdk))
    if not is_termux_runtime(prefix):
        return False
    aapt2 = prefix / "bin/aapt2"
    if not is_native_termux_aapt2(aapt2):
        raise ConfigurationError(f"{aapt2} is not a native Termux AAPT2 (ELF64 AArch64, linker64)")

    settings_path = home / ".flutter_settings"
    settings, settings_stat = _read_json(settings_path)
    if settings.get("android-sdk") != str(android_sdk):
        settings["android-sdk"] = str(android_sdk)
        settings_bytes = (json.dumps(settings, indent=2, ensure_ascii=False) + "\n").encode()
    else:
        settings_bytes = None

    gradle_home = gradle_user_home or Path(os.environ.get("GRADLE_USER_HOME", home / ".gradle"))
    properties_path = gradle_home / "gradle.properties"
    properties, properties_stat, override = _read_properties(properties_path)
    if override is not None:
        if not is_native_termux_aapt2(Path(override).expanduser()):
            raise ConfigurationError("configured android.aapt2FromMavenOverride is not a native Termux AAPT2; preserving user configuration")
        properties_bytes = None
    else:
        separator = "" if not properties or properties.endswith("\n") else "\n"
        encoded_path = _escape_java_property_value(str(aapt2))
        properties_bytes = (properties + separator + f"android.aapt2FromMavenOverride={encoded_path}\n").encode()

    # Both existing files are parsed and validated before either live file changes.
    if settings_bytes is not None:
        _atomic_write(settings_path, settings_bytes, settings_stat)
    if properties_bytes is not None:
        _atomic_write(properties_path, properties_bytes, properties_stat)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--android-sdk", type=Path, required=True)
    parser.add_argument("--gradle-user-home", type=Path)
    args = parser.parse_args()
    try:
        applied = configure(args.prefix, args.home, args.android_sdk, args.gradle_user_home)
    except ConfigurationError as exc:
        parser.error(str(exc))
    if applied:
        print(f"Configured Android SDK and Termux AAPT2: {args.android_sdk}")
    else:
        print("Skipping persistent Android configuration outside native Termux")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
