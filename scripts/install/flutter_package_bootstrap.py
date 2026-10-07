#!/usr/bin/env python3
"""Validate the packaged Flutter toolchain before configuring Termux Android paths."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

try:  # Checkout tests import this as a package; installed helper is a sibling script.
    from . import configure_android as _android_config
except ImportError:  # pragma: no cover - exercised by installed-script smoke tests.
    import configure_android as _android_config


class BootstrapError(RuntimeError):
    """A required packaged artifact or safe configuration precondition is invalid."""


configure_android = _android_config.configure


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise BootstrapError(f"required Flutter package file is missing or unreadable: {path}: {exc}") from exc


def _require_file(path: Path, *, executable: bool = False, nonempty: bool = True,
                  allow_internal_symlink: bool = False, root: Path | None = None) -> None:
    if path.is_symlink() and not allow_internal_symlink:
        raise BootstrapError(f"required Flutter package file is missing or unsafe: {path}")
    if allow_internal_symlink:
        try:
            resolved = path.resolve(strict=True)
            if root is None:
                raise ValueError("internal symlink check needs package root")
            resolved.relative_to(root.resolve())
        except (OSError, ValueError) as exc:
            raise BootstrapError(f"required Flutter package symlink escapes or is broken: {path}") from exc
    if not path.is_file():
        raise BootstrapError(f"required Flutter package file is missing or unsafe: {path}")
    info = path.stat()
    if nonempty and info.st_size == 0:
        raise BootstrapError(f"required Flutter package file is empty: {path}")
    if executable and not info.st_mode & 0o111:
        raise BootstrapError(f"required Flutter package executable is not executable: {path}")


def _require_android_elf(path: Path) -> None:
    _require_file(path, executable=True)
    try:
        data = path.read_bytes()
        if len(data) < 64 or data[:7] != b"\x7fELF\x02\x01\x01":
            raise ValueError("not ELF64 little-endian")
        if struct.unpack_from("<H", data, 18)[0] != 183:
            raise ValueError("not AArch64")
        phoff = struct.unpack_from("<Q", data, 32)[0]
        phentsize, phnum = struct.unpack_from("<HH", data, 54)
        if phentsize < 56 or phoff + phentsize * phnum > len(data):
            raise ValueError("invalid program header table")
        interpreters = []
        for index in range(phnum):
            offset = phoff + index * phentsize
            if struct.unpack_from("<I", data, offset)[0] == 3:
                p_offset, p_size = struct.unpack_from("<QQ", data, offset + 8)[0], struct.unpack_from("<Q", data, offset + 32)[0]
                if p_offset + p_size > len(data):
                    raise ValueError("invalid interpreter range")
                interpreters.append(data[p_offset:p_offset + p_size].rstrip(b"\0").decode("ascii"))
        if interpreters != ["/system/bin/linker64"]:
            raise ValueError(f"wrong Android interpreter {interpreters!r}")
    except (OSError, ValueError, struct.error) as exc:
        raise BootstrapError(f"invalid Android ARM64 executable {path}: {exc}") from exc


def _package_roots(root: Path, config_path: Path) -> tuple[dict, list[Path]]:
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"FlutterTools package_config.json is missing or invalid: {exc}") from exc
    if not isinstance(config, dict) or config.get("configVersion") != 2 or not isinstance(config.get("packages"), list):
        raise BootstrapError("FlutterTools package_config.json has an unsupported structure")
    roots: list[Path] = []
    sdk = root.resolve()
    for item in config["packages"]:
        if not isinstance(item, dict) or not isinstance(item.get("rootUri"), str):
            raise BootstrapError("FlutterTools package_config.json contains an invalid package entry")
        parsed = urlparse(item["rootUri"])
        if parsed.scheme:
            raise BootstrapError(f"FlutterTools package root must be relative to the SDK: {item['rootUri']}")
        candidate = config_path.parent / unquote(item["rootUri"])
        try:
            candidate = candidate.resolve(strict=True)
            candidate.relative_to(sdk)
        except (OSError, ValueError) as exc:
            raise BootstrapError(f"FlutterTools package root escapes or is missing from the packaged SDK: {item['rootUri']}") from exc
        if not candidate.is_dir():
            raise BootstrapError(f"FlutterTools package root is not a directory: {candidate}")
        roots.append(candidate)
    return config, roots


def _validate_git_key(root: Path, tool_args: str) -> str:
    git_dir = root / ".git"
    marker = git_dir / "termux_synthetic"
    if not git_dir.is_dir() or git_dir.is_symlink() or not marker.is_file():
        raise BootstrapError("packaged minimal synthetic Git metadata is missing; refusing an engine-version fallback")
    git = shutil.which("git")
    if not git:
        raise BootstrapError("Git is required to validate the packaged FlutterTools snapshot stamp")
    result = subprocess.run([git, "-C", str(root), "rev-parse", "HEAD"], text=True, capture_output=True, check=False)
    revision = result.stdout.strip()
    if result.returncode or len(revision) != 40 or any(ch not in "0123456789abcdef" for ch in revision.lower()):
        raise BootstrapError("packaged synthetic Git repository has no valid HEAD commit")
    stamp = root / "bin/cache/flutter_tools.stamp"
    if _read(stamp) != f"{revision}:{tool_args}":
        raise BootstrapError("packaged FlutterTools stamp does not match synthetic Git HEAD and FLUTTER_TOOL_ARGS")
    return revision


def _validate_engine_metadata(cache: Path, engine_version: Path) -> None:
    try:
        marker = _read(cache / "engine_stamp.stamp")
        engine = _read(engine_version)
        data = json.loads((cache / "engine_stamp.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"required packaged engine metadata is missing or invalid: {exc}") from exc
    if not isinstance(data, dict) or data.get("git_revision") != engine or marker != engine:
        raise BootstrapError("packaged engine_stamp.json and cache markers do not agree")
    if (type(data.get("build_time_ms")) is not int or not data.get("content_hash")
            or not isinstance(data.get("git_revision_date"), str)):
        raise BootstrapError("packaged engine_stamp.json lacks required Flutter cache fields")


def _validate_devtools_metadata(path: Path) -> None:
    try:
        _require_file(path)
    except BootstrapError as exc:
        raise BootstrapError(f"Flutter DevTools version metadata is missing or invalid: {exc}") from exc
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"Flutter DevTools version metadata is missing or invalid: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("version"), str) or not value["version"].strip():
        raise BootstrapError("Flutter DevTools version metadata must contain a nonempty string version")


def _bounded_diagnostic(value: str, limit: int = 1200) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    marker = "\n... diagnostic truncated ...\n"
    edge = max(1, (limit - len(marker)) // 2)
    return text[:edge] + marker + text[-edge:]


def _isolated_snapshot_load(root: Path, dart: Path, snapshot: Path, config_path: Path,
                            config: dict, roots: list[Path], tool_args: str) -> None:
    """Load the real packaged snapshot against dereferenced, private source inputs."""
    with tempfile.TemporaryDirectory(prefix="flutter-package-check-") as temporary:
        temp = Path(temporary)
        isolated = temp / "flutter"
        isolated.mkdir()
        copy_roots = {root / "packages/flutter_tools", *roots}
        for source in sorted(copy_roots, key=lambda path: len(path.parts)):
            relative = source.relative_to(root)
            destination = isolated / relative
            if destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination, symlinks=False)
        for relative in ("bin/internal/engine.version", "bin/version", "version"):
            source = root / relative
            if source.is_file() and not source.is_symlink():
                destination = isolated / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        if (root / ".git").is_dir():
            shutil.copytree(root / ".git", isolated / ".git", symlinks=False)
        cache = isolated / "bin/cache"
        cache.mkdir(parents=True, exist_ok=True)
        for name in ("flutter_tools.snapshot", "engine.stamp", "engine_stamp.stamp", "engine_stamp.json"):
            source = root / "bin/cache" / name
            _require_file(source, allow_internal_symlink=(name == "flutter_tools.snapshot"), root=root)
            shutil.copy2(source, cache / name)
        devtools_source = root / "bin/cache/dart-sdk/bin/resources/devtools/version.json"
        _validate_devtools_metadata(devtools_source)
        devtools_destination = cache / "dart-sdk/bin/resources/devtools/version.json"
        devtools_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(devtools_source, devtools_destination)
        isolated_config_path = isolated / "packages/flutter_tools/.dart_tool/package_config.json"
        copied_config = dict(config)
        copied_packages = []
        for item in config["packages"]:
            entry = dict(item)
            parsed = urlparse(entry["rootUri"])
            source = config_path.parent / unquote(entry["rootUri"])
            copied_root = isolated / source.resolve().relative_to(root.resolve())
            entry["rootUri"] = copied_root.as_uri()
            copied_packages.append(entry)
        copied_config["packages"] = copied_packages
        isolated_config_path.parent.mkdir(parents=True, exist_ok=True)
        isolated_config_path.write_text(json.dumps(copied_config), encoding="utf-8")
        private_home, private_pub, private_tmp = (temp / name for name in ("home", "pub-cache", "tmp"))
        for path in (private_home, private_pub, private_tmp):
            path.mkdir()
        env = os.environ.copy()
        env.update({"FLUTTER_ROOT": str(isolated), "HOME": str(private_home),
                    "PUB_CACHE": str(private_pub), "TMPDIR": str(private_tmp),
                    "FLUTTER_SUPPRESS_ANALYTICS": "true", "CI": "true"})
        args = [str(dart), f"--packages={isolated_config_path}", *tool_args.split(),
                str(cache / "flutter_tools.snapshot"), "--no-version-check", "--suppress-analytics", "--version"]
        try:
            result = subprocess.run(args, cwd=isolated, env=env, text=True, capture_output=True,
                                    timeout=180, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise BootstrapError(f"isolated FlutterTools snapshot load could not run: {exc}") from exc
        if result.returncode != 0 or "Flutter" not in (result.stdout + result.stderr):
            detail = _bounded_diagnostic(result.stderr + "\n" + result.stdout)
            raise BootstrapError(f"isolated FlutterTools snapshot load failed ({result.returncode}): {detail}")


def validate_package(prefix: Path, flutter_root: Path, android_sdk: Path, tool_args: str | None = None) -> None:
    prefix, root, sdk = map(Path, (prefix, flutter_root, android_sdk))
    if root.is_symlink() or not root.is_dir():
        raise BootstrapError(f"packaged Flutter SDK is missing or unsafe: {root}")
    cache = root / "bin/cache"
    dart_dir = cache / "dart-sdk/bin"
    for executable in (dart_dir / "dart", dart_dir / "dartvm", dart_dir / "dartaotruntime",
                       cache / "artifacts/engine/android-arm64-release/linux-arm64/gen_snapshot",
                       cache / "artifacts/engine/android-arm64-profile/linux-arm64/gen_snapshot"):
        _require_android_elf(executable)
    tools = root / "packages/flutter_tools"
    entry = tools / "bin/flutter_tools.dart"
    config_path = tools / ".dart_tool/package_config.json"
    for path in (entry, tools / "pubspec.yaml", tools / "pubspec.lock", root / "bin/internal/shared.sh",
                 cache / "flutter_tools.stamp"):
        _require_file(path)
    _require_file(cache / "flutter_tools.snapshot", allow_internal_symlink=True, root=root)
    yaml, lock = tools / "pubspec.yaml", tools / "pubspec.lock"
    if yaml.stat().st_mtime_ns > lock.stat().st_mtime_ns:
        raise BootstrapError("packaged FlutterTools pubspec.lock is stale; package must resolve dependencies before installation")
    config, roots = _package_roots(root, config_path)
    engine_version = root / "bin/internal/engine.version"
    _validate_engine_metadata(cache, engine_version)
    _validate_devtools_metadata(dart_dir / "resources/devtools/version.json")
    if tool_args is None:
        tool_args = os.environ.get("FLUTTER_TOOL_ARGS", "")
    _validate_git_key(root, tool_args)
    if not (sdk / "platforms/android-34/android.jar").is_file():
        raise BootstrapError(f"Android SDK is incomplete (platforms/android-34/android.jar missing): {sdk}")
    if (not (sdk / "licenses").is_dir() or not (sdk / "platform-tools/adb").is_file()
            or not (sdk / "build-tools").is_dir() or not any((sdk / "build-tools").iterdir())):
        raise BootstrapError(f"Android SDK is incomplete (licenses, build-tools or platform-tools/adb missing): {sdk}")
    dart = dart_dir / "dart"
    snapshot = cache / "flutter_tools.snapshot"
    _isolated_snapshot_load(root, dart, snapshot, config_path, config, roots, tool_args)


def configure_package(prefix: Path, home: Path, flutter_root: Path, android_sdk: Path,
                      tool_args: str | None = None) -> None:
    prefix, home, root, sdk = map(Path, (prefix, home, flutter_root, android_sdk))
    if not home.is_dir() or home.is_symlink():
        raise BootstrapError(f"HOME must already exist as a real directory; refusing unsafe configuration: {home}")
    if not _android_config.is_termux_runtime(prefix):
        raise BootstrapError("automatic package bootstrap is supported only in the native Termux runtime")
    validate_package(prefix, root, sdk, tool_args)
    try:
        if not configure_android(prefix, home, sdk):
            raise BootstrapError("Termux Android configuration was not applied")
    except _android_config.ConfigurationError as exc:
        raise BootstrapError(f"Android SDK/AAPT2 configuration failed: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--flutter-root", type=Path, required=True)
    parser.add_argument("--android-sdk", type=Path, required=True)
    args = parser.parse_args()
    try:
        configure_package(args.prefix, args.home, args.flutter_root, args.android_sdk)
    except BootstrapError as exc:
        parser.error(str(exc))
    print(f"Validated Flutter package and configured Android SDK: {args.android_sdk}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
