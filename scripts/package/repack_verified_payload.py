#!/usr/bin/env python3
"""Rebuild the verified ARM64 Termux payload with an offline FlutterTools kernel."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote, urlparse


SOURCE_SHA256 = "f3236fdc40cf85712aacea5ccb8d55611f76d6380c12f19276566f27db22188c"
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
FLUTTER_VERSION = "3.47.2"
PACKAGE_VERSION = "3.47.2-4"
PREFIX_RELATIVE = Path("data/data/com.termux/files/usr")
FLUTTER_RELATIVE = PREFIX_RELATIVE / "opt/flutter"
AT_FDCWD = -100
RENAME_NOREPLACE = 1
PUBLISHED_RESOURCES = (
    "flutter",
    "flutter_tools_git",
    "executable",
    "profile",
    "version_json",
    "canonical_manifest",
    "post_install",
    "configure_android",
    "flutter_package_bootstrap",
    "release_engine_stripper",
    "verified_engine_cache",
)
PATCH_FUNCTIONS = (
    ("patch_flutter_cache", "packages/flutter_tools/lib/src/flutter_cache.dart"),
    ("patch_gradle_wrapper", "packages/flutter_tools/lib/src/android/gradle_utils.dart"),
    ("patch_create_gradle_wrapper", "packages/flutter_tools/lib/src/commands/create_base.dart"),
    ("patch_compile_sdk", "packages/flutter_tools/gradle/src/main/kotlin/FlutterExtension.kt"),
    ("patch_plugin_utils", "packages/flutter_tools/gradle/src/main/kotlin/FlutterPluginUtils.kt"),
    ("patch_release_engine_strip", "packages/flutter_tools/gradle/src/main/kotlin/FlutterPlugin.kt"),
    ("patch_shebang_flutter", "bin/flutter"),
    ("patch_shebang_shared", "bin/internal/shared.sh"),
    ("patch_shebang_update_dart", "bin/internal/update_dart_sdk.sh"),
    ("patch_shebang_update_engine", "bin/internal/update_engine_version.sh"),
    ("patch_shebang_tool_backend", "packages/flutter_tools/bin/tool_backend.sh"),
)


class RepackError(RuntimeError):
    """The pinned payload or a required offline repack invariant is invalid."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_paths(source: Path, output: Path) -> tuple[Path, Path]:
    source = Path(source).expanduser().absolute()
    output = Path(output).expanduser().absolute()
    if source == output:
        raise RepackError("source and output are the same file")
    if not source.is_file() or source.is_symlink():
        raise RepackError(f"verified source archive is missing or unsafe: {source}")
    if os.path.lexists(output):
        try:
            if source.samefile(output):
                raise RepackError("source and output are the same file")
        except FileNotFoundError:
            pass
        raise RepackError(f"output already exists; refusing to overwrite: {output}")
    return source, output


def verify_source(source: Path) -> str:
    source = Path(source)
    actual = sha256_file(source)
    if actual != SOURCE_SHA256:
        raise RepackError(f"source archive SHA-256 mismatch: expected {SOURCE_SHA256}, found {actual}")
    return actual


def normalize_package_config(flutter_root: Path, config_path: Path) -> list[Path]:
    """Rebase pinned archive cache roots, then strictly normalize contained roots."""
    flutter_root, config_path = Path(flutter_root).resolve(), Path(config_path)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RepackError(f"FlutterTools package roots cannot be normalized safely: {exc}") from exc
    if not isinstance(config, dict) or config.get("configVersion") != 2 or not isinstance(config.get("packages"), list):
        raise RepackError("FlutterTools package_config.json has an unsupported structure")
    _rebase_pinned_archive_cache_roots(flutter_root, config_path, config)
    try:
        # This build helper percent-decodes before strict containment validation.
        from build import normalize_flutter_tools_package_config

        normalize_flutter_tools_package_config(config_path, flutter_root)
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RepackError(f"FlutterTools package roots cannot be normalized safely: {exc}") from exc
    roots: list[Path] = []
    for entry in config["packages"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("rootUri"), str):
            raise RepackError("FlutterTools package_config.json contains an invalid root URI")
        parsed = urlparse(entry["rootUri"])
        if parsed.scheme or parsed.netloc:
            raise RepackError(f"package root URI must be contained in the Flutter SDK: {entry['rootUri']}")
        candidate = (config_path.parent / unquote(parsed.path)).resolve()
        try:
            candidate.relative_to(flutter_root)
        except ValueError as exc:
            raise RepackError(f"package root escapes or is missing from the Flutter SDK: {entry['rootUri']}") from exc
        if not candidate.is_dir():
            raise RepackError(f"package root escapes or is missing from the Flutter SDK: {entry['rootUri']}")
        roots.append(candidate)
    return roots


def _rebase_pinned_archive_cache_roots(flutter_root: Path, config_path: Path,
                                       config: dict) -> None:
    """Map only one pinned archive's absolute ``flutter/.pub-cache`` prefix."""
    packages = config["packages"]
    tool_entries = [entry for entry in packages if isinstance(entry, dict)
                    and entry.get("name") == "flutter_tools"]
    if len(tool_entries) != 1 or not isinstance(tool_entries[0].get("rootUri"), str):
        raise RepackError("FlutterTools package_config.json has no unique flutter_tools anchor")
    tool_uri = urlparse(tool_entries[0]["rootUri"])
    if tool_uri.scheme not in ("", "file") or tool_uri.netloc not in ("", "localhost"):
        raise RepackError("FlutterTools package_config.json has an unsafe flutter_tools anchor")
    tool_path = Path(unquote(tool_uri.path)) if tool_uri.scheme == "file" else config_path.parent / unquote(tool_entries[0]["rootUri"])
    try:
        if tool_path.resolve(strict=True) != (flutter_root / "packages/flutter_tools").resolve(strict=True):
            raise RepackError("FlutterTools package_config.json anchor does not identify this SDK")
    except OSError as exc:
        raise RepackError(f"FlutterTools package_config.json anchor is missing: {exc}") from exc

    absolute_entries: list[tuple[dict, Path]] = []
    prefixes: set[Path] = set()
    for entry in packages:
        if not isinstance(entry, dict) or not isinstance(entry.get("rootUri"), str):
            raise RepackError("FlutterTools package_config.json contains an invalid root URI")
        parsed = urlparse(entry["rootUri"])
        if parsed.scheme != "file" or not parsed.path.startswith("/"):
            continue
        if parsed.netloc not in ("", "localhost") or parsed.query or parsed.fragment:
            raise RepackError(f"absolute package root URI is not a local archive path: {entry['rootUri']}")
        decoded = unquote(parsed.path)
        parts = Path(decoded).parts
        if ".." in parts:
            raise RepackError(f"absolute package root URI contains traversal: {entry['rootUri']}")
        markers = [index for index, component in enumerate(parts) if component == ".pub-cache"]
        if len(markers) != 1 or markers[0] < 2 or parts[markers[0] - 1] != "flutter":
            raise RepackError(f"absolute package root URI has no verified Flutter cache prefix: {entry['rootUri']}")
        marker = markers[0]
        prefix, suffix = Path(*parts[:marker]), Path(*parts[marker + 1:])
        if not suffix.parts:
            raise RepackError(f"absolute package root URI does not identify a package: {entry['rootUri']}")
        prefixes.add(prefix)
        absolute_entries.append((entry, suffix))
    if len(prefixes) > 1:
        raise RepackError("absolute package roots use inconsistent archived source prefixes")
    if not absolute_entries:
        return

    # The archive SHA is verified before this function runs, binding this unique
    # source prefix to the anchored FlutterTools package configuration.
    for entry, suffix in absolute_entries:
        candidate = (flutter_root / ".pub-cache" / suffix).resolve()
        try:
            candidate.relative_to(flutter_root)
        except ValueError as exc:
            raise RepackError(f"package root escapes or is missing from the Flutter SDK: {entry['rootUri']}") from exc
        if not candidate.is_dir():
            raise RepackError(f"package root escapes or is missing from the Flutter SDK: {entry['rootUri']}")
        relative = os.path.relpath(candidate, config_path.parent)
        entry["rootUri"] = quote(relative, safe="/.-_~") + "/"
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def validate_engine_metadata(engine_version: Path, engine_stamp: Path, metadata_path: Path) -> str:
    try:
        revision = Path(engine_version).read_text(encoding="utf-8").strip()
        stamp = Path(engine_stamp).read_text(encoding="utf-8").strip()
        metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepackError(f"required engine metadata is missing or invalid: {exc}") from exc
    if not isinstance(metadata, dict):
        raise RepackError("engine metadata does not match the archived engine revision")
    if (len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision.lower())
            or stamp != revision or metadata.get("git_revision") != revision
            or type(metadata.get("build_time_ms")) is not int
            or not isinstance(metadata.get("content_hash"), str) or not metadata["content_hash"].strip()
            or not isinstance(metadata.get("git_revision_date"), str) or not metadata["git_revision_date"].strip()):
        raise RepackError("engine metadata does not match the archived engine revision")
    return revision


def native_inventory(root: Path) -> dict[str, str]:
    """Hash every ELF payload below a staged SDK, including executable and shared objects."""
    root = Path(root)
    result = {}
    for path in root.rglob("*"):
        if path.is_file():
            try:
                with path.open("rb") as stream:
                    if stream.read(4) == b"\x7fELF":
                        result[path.relative_to(root).as_posix()] = sha256_file(path)
            except OSError as exc:
                raise RepackError(f"cannot inspect native payload {path}: {exc}") from exc
    return dict(sorted(result.items()))


def assert_native_inventory_equal(before_root: Path, after_root: Path) -> None:
    before, after = native_inventory(before_root), native_inventory(after_root)
    if before != after:
        changed = sorted(set(before) ^ set(after) | {name for name in before.keys() & after.keys() if before[name] != after[name]})
        raise RepackError("native payload changed while repacking: " + ", ".join(changed[:12]))


def write_provenance(path: Path, data: dict) -> None:
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run(argv: list[str], *, cwd: Path | None = None, env: dict | None = None,
         timeout: int = 300) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True,
                                timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RepackError(f"command could not run ({Path(argv[0]).name}): {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1800:]
        raise RepackError(f"command failed ({result.returncode}): {Path(argv[0]).name}: {detail}")
    return result


def _extract_archive(source: Path, payload: Path, control: Path) -> None:
    _run(["dpkg-deb", "-x", str(source), str(payload)])
    _run(["dpkg-deb", "-e", str(source), str(control)])


def _verify_source_metadata(source: Path, expected_sha: str) -> str:
    metadata_path = source.with_name("build_metadata.json")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepackError(f"source build_metadata.json is missing or invalid: {exc}") from exc
    if (not isinstance(metadata, dict) or metadata.get("sha256") != expected_sha or metadata.get("version") != FLUTTER_VERSION
            or metadata.get("arch") != "arm64" or not metadata.get("source_commit")):
        raise RepackError("source build metadata does not identify the pinned Flutter 3.47.2 ARM64 archive")
    return str(metadata["source_commit"])


def _apply_required_patches(root: Path, installer: Path, private: Path) -> None:
    missing = [relative for _, relative in PATCH_FUNCTIONS if not (root / relative).is_file()]
    if missing:
        raise RepackError("pinned payload is missing source patch targets: " + ", ".join(missing))
    for _, relative in PATCH_FUNCTIONS:
        target = root / relative
        try:
            target.resolve(strict=True).relative_to(root.resolve())
        except (OSError, ValueError) as exc:
            raise RepackError(f"source patch target escapes or is missing from the staged SDK: {relative}") from exc
    calls = "\n".join(f"{name} \"$FLUTTER_ROOT/{relative}\"" for name, relative in PATCH_FUNCTIONS)
    script = 'set -e\nsource "$1" --lib\n' + calls + "\n"
    env = os.environ.copy()
    env.update({
        "PREFIX": "/data/data/com.termux/files/usr",
        "FLUTTER_ROOT": str(root),
        "HOME": str(private / "home"),
        "TMPDIR": str(private / "tmp"),
        "PATCH_STATE_FILE": str(private / "patch_state.json"),
        "BACKUP_DIR": str(private / "backups"),
    })
    for name in ("home", "tmp", "backups"):
        (private / name).mkdir(parents=True, exist_ok=True)
    _run(["bash", "-c", script, "repack", str(installer)], env=env, timeout=120)


def _synthetic_git(root: Path, *, private: Path) -> str:
    git_dir = root / ".git"
    cache_git = root / "bin/cache/flutter_tools_git"
    if git_dir.exists() or git_dir.is_symlink() or cache_git.exists() or cache_git.is_symlink():
        raise RepackError("source archive unexpectedly contains Git metadata; refusing to replace it")
    git = shutil.which("git")
    if not git:
        raise RepackError("Git is required to create the deterministic package compile key")
    env = os.environ.copy()
    env.update({
        "GIT_CONFIG_GLOBAL": str(private / "empty-gitconfig"),
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "Termux Flutter package",
        "GIT_AUTHOR_EMAIL": "flutter-package@localhost",
        "GIT_COMMITTER_NAME": "Termux Flutter package",
        "GIT_COMMITTER_EMAIL": "flutter-package@localhost",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
    })
    private.mkdir(parents=True, exist_ok=True)
    Path(env["GIT_CONFIG_GLOBAL"]).write_text("", encoding="utf-8")
    _run([git, "init", "-q", str(root)], env=env)
    _run([git, "-C", str(root), "symbolic-ref", "HEAD", "refs/heads/stable"], env=env)
    _run([git, "-C", str(root), "config", "core.autocrlf", "false"], env=env)
    files = ["bin/flutter", "bin/internal/engine.version"]
    candidate = root / "bin/internal/release-candidate-branch.version"
    if candidate.is_file():
        files.append("bin/internal/release-candidate-branch.version")
    for relative in files:
        if not (root / relative).is_file() or (root / relative).is_symlink():
            raise RepackError(f"synthetic Git input is missing or unsafe: {relative}")
    _run([git, "-C", str(root), "add", "-f", *files], env=env)
    _run([git, "-C", str(root), "commit", "-q", "--no-gpg-sign", "-m", "Termux Flutter 3.47.2 package source"], env=env)
    _run([git, "-C", str(root), "tag", FLUTTER_VERSION], env=env)
    revision = _run([git, "-C", str(root), "rev-parse", "HEAD"], env=env).stdout.strip()
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
        raise RepackError("Git did not produce a full synthetic commit id")
    (git_dir / "termux_synthetic").write_text("FlutterTools compile key only; see termux_repack_provenance.json\n", encoding="utf-8")
    shutil.copytree(git_dir, cache_git)
    return revision


def _package_object(root: Path, repo_root: Path, package_yaml: dict):
    import package as package_builder

    resources = {name: package_yaml["resource"][name] for name in PUBLISHED_RESOURCES
                 if name in package_yaml["resource"]}
    # These checked-in scripts are normally adjacent to the checkout's Flutter source.
    # Rebind only their current package.yaml resource entries to this checkout.
    resources["post_install"] = dict(resources["post_install"], source=str(repo_root / "scripts/install/post_install.sh"))
    resources["configure_android"] = dict(resources["configure_android"], source=str(repo_root / "scripts/install/configure_android.py"))
    resources["flutter_package_bootstrap"] = dict(resources["flutter_package_bootstrap"], source=str(repo_root / "scripts/install/flutter_package_bootstrap.py"))
    resources["release_engine_stripper"] = dict(resources["release_engine_stripper"], source=str(repo_root / "scripts/install/strip_release_engine.py"))
    engine_cache = Path(root) / "bin/cache/artifacts/engine"
    if engine_cache.is_symlink() or not engine_cache.is_dir():
        raise RepackError(f"verified archive is missing its runtime engine cache tree: {engine_cache}")
    # package.py's generic source-tree walker intentionally excludes directories named
    # "engine"; map only the existing runtime cache subtree back into its fixed SDK path.
    resources["verified_engine_cache"] = {
        "source": str(engine_cache),
        "output": str(FLUTTER_RELATIVE / "bin/cache/artifacts/engine"),
    }
    return package_builder.Package(
        root=root,
        arch="arm64",
        control=package_yaml["control"],
        resource=resources,
        define=package_yaml.get("define"),
        tag=FLUTTER_VERSION,
        revision=PACKAGE_VERSION.rsplit("-", 1)[1],
        maintainer_scripts={"postinst": repo_root / "scripts/install/postinst.sh"},
    )


def validate_package_native_inventory(package, flutter_root: Path,
                                      expected_inventory: dict[str, str] | None = None) -> dict[str, str]:
    """Fail before compression unless selected resource routes preserve every staged ELF byte."""
    flutter_root = Path(flutter_root)
    expected = expected_inventory if expected_inventory is not None else native_inventory(flutter_root)
    prefix = FLUTTER_RELATIVE
    routed: dict[str, str | None] = {}
    for item in package.gen_resource(list(PUBLISHED_RESOURCES)):
        destination = Path(item["out"])
        try:
            relative = destination.relative_to(prefix).as_posix()
        except ValueError:
            continue
        source = item.get("src")
        if isinstance(source, bytes):
            digest = hashlib.sha256(source).hexdigest() if source.startswith(b"\x7fELF") else None
        elif isinstance(source, Path) and source.is_file():
            try:
                with source.open("rb") as stream:
                    is_elf = stream.read(4) == b"\x7fELF"
                digest = sha256_file(source) if is_elf else None
            except OSError as exc:
                raise RepackError(f"cannot inspect packaged source for {relative}: {exc}") from exc
        else:
            digest = None
        # This matches Package.debuild's final-writer behavior for declared overlays.
        routed[relative] = digest
    packaged = {path: digest for path, digest in routed.items() if digest is not None}
    if packaged != expected:
        missing = sorted(set(expected) - set(packaged))
        unexpected = sorted(set(packaged) - set(expected))
        changed = sorted(path for path in set(expected) & set(packaged) if expected[path] != packaged[path])
        details = []
        if missing:
            details.append("missing=" + ",".join(missing[:8]))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected[:8]))
        if changed:
            details.append("changed=" + ",".join(changed[:8]))
        raise RepackError("selected package resources do not preserve staged native ELF inventory: "
                          + "; ".join(details))
    return packaged


def validate_package_payload_routes(package, flutter_root: Path,
                                    package_roots: list[Path]) -> dict:
    """Preflight every resolved package member and mandatory non-ELF resource route."""
    import build

    flutter_root = Path(flutter_root).resolve(strict=True)
    routes: dict[str, Path | None] = {}
    resource_items = list(package.gen_resource(list(PUBLISHED_RESOURCES)))
    for item in resource_items:
        destination = Path(item["out"])
        try:
            relative = destination.relative_to(FLUTTER_RELATIVE).as_posix()
        except ValueError:
            continue
        source = item.get("src")
        routes[relative] = source if isinstance(source, Path) else None

    members: dict[str, str] = {}
    for package_root in package_roots:
        package_root = Path(package_root).resolve(strict=True)
        try:
            package_root.relative_to(flutter_root)
        except ValueError as exc:
            raise RepackError(f"package root escapes the staged Flutter SDK: {package_root}") from exc
        for source in (package_root, *package_root.rglob("*")):
            if source.is_symlink():
                try:
                    source.resolve(strict=True).relative_to(flutter_root)
                except (OSError, ValueError) as exc:
                    raise RepackError(f"package member symlink escapes or is missing: {source}") from exc
            relative = source.relative_to(flutter_root).as_posix()
            routed_source = routes.get(relative)
            if routed_source is None or routed_source != source:
                raise RepackError(f"package payload route is missing or mismatched: {relative}")
            if source.is_file():
                members[relative] = sha256_file(source)

    required_sdk_paths = [
        suffix.removeprefix("opt/flutter/")
        for suffix in build.REQUIRED_DEB_TOOL_ARTIFACTS
        if suffix.startswith("opt/flutter/")
    ]
    for relative in required_sdk_paths:
        source = flutter_root / relative
        routed_source = routes.get(relative)
        if (not source.is_file() or not isinstance(routed_source, Path)
                or not routed_source.is_file()
                or sha256_file(source) != sha256_file(routed_source)):
            raise RepackError(f"required FlutterTools input is not routed into the deb: {relative}")

    required_outputs = {(PREFIX_RELATIVE / suffix).as_posix()
                        for suffix in build.REQUIRED_DEB_INSTALLER_ARTIFACTS}
    output_items = {Path(item["out"]).as_posix(): item for item in resource_items}
    output_routes = set(output_items)
    missing_outputs = sorted(required_outputs - output_routes)
    if missing_outputs:
        raise RepackError("required installer resources are not routed into the deb: "
                          + ", ".join(missing_outputs))
    for output in sorted(required_outputs):
        item = output_items[output]
        source, mode = item.get("src"), item.get("mod")
        executable = (mode & 0o111) if isinstance(mode, int) else (
            source.stat().st_mode & 0o111 if isinstance(source, Path) and source.is_file() else 0)
        if (isinstance(source, bytes) and not source) or not isinstance(source, (bytes, Path)):
            raise RepackError(f"required installer resource has no file payload: {output}")
        if isinstance(source, Path) and not source.is_file():
            raise RepackError(f"required installer resource source is missing: {source}")
        if executable == 0:
            raise RepackError(f"required installer resource is not executable: {output}")

    control_items = list(package.gen_control_files())
    scripts = {item.get("out"): item for item in control_items if item.get("out") != "control"}
    postinst = scripts.get("postinst")
    if (postinst is None or not isinstance(postinst.get("src"), bytes)
            or not postinst["src"].startswith(b"#!")
            or not isinstance(postinst.get("mod"), int) or postinst["mod"] & 0o111 == 0):
        raise RepackError("package control metadata does not provide an executable postinst")

    manifest_digest = hashlib.sha256(json.dumps(members, sort_keys=True, separators=(",", ":"))
                                     .encode("utf-8")).hexdigest()
    return {
        "package_root_count": len(package_roots),
        "package_root_file_count": len(members),
        "package_root_members_sha256": manifest_digest,
        "required_tool_input_count": len(required_sdk_paths),
        "required_installer_output_count": len(required_outputs),
        "control_file_count": len(control_items),
        "maintainer_script_count": len(scripts),
    }


def _apply_overlay(payload_root: Path, items: list[dict]) -> None:
    """Apply selected package.yaml resources into an extracted payload for offline loading."""
    payload_root = Path(payload_root)
    for item in items:
        out = Path(item["out"])
        if out.is_absolute() or ".." in out.parts:
            raise RepackError(f"unsafe package resource destination: {out}")
        destination = payload_root / out
        for parent in destination.parents:
            if parent == payload_root:
                break
            if parent.is_symlink():
                raise RepackError(f"package resource destination traverses a staged symlink: {parent}")
        source = item.get("src")
        mode = item.get("mod")
        if destination.is_symlink():
            if isinstance(source, bytes) or isinstance(source, Path) and source.is_file():
                destination.unlink()
            else:
                raise RepackError(f"package resource cannot replace a staged directory symlink: {destination}")
        if isinstance(source, bytes):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source)
        elif source is None or (isinstance(source, Path) and source.is_dir()):
            destination.mkdir(parents=True, exist_ok=True)
            continue
        elif isinstance(source, Path) and source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                destination.unlink(missing_ok=True)
                destination.symlink_to(os.readlink(source))
            else:
                shutil.copy2(source, destination)
        else:
            raise RepackError(f"package resource source is missing: {source}")
        if mode is not None and destination.exists() and not destination.is_symlink():
            destination.chmod(mode)


def _compile_and_load(root: Path, tool_args: str, private: Path) -> tuple[str, int]:
    cache = root / "bin/cache"
    dart = cache / "dart-sdk/bin/dart"
    entry = root / "packages/flutter_tools/bin/flutter_tools.dart"
    config = root / "packages/flutter_tools/.dart_tool/package_config.json"
    snapshot = cache / "flutter_tools.snapshot"
    stamp = cache / "flutter_tools.stamp"
    for name in ("home", "pub", "tmp"):
        (private / name).mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "FLUTTER_ROOT": str(root),
        "HOME": str(private / "home"),
        "XDG_CONFIG_HOME": str(private / "home/.config"),
        "XDG_CACHE_HOME": str(private / "home/.cache"),
        "XDG_DATA_HOME": str(private / "home/.local/share"),
        "GRADLE_USER_HOME": str(private / "home/.gradle"),
        "ANDROID_USER_HOME": str(private / "home/.android"),
        "ANDROID_SDK_HOME": str(private / "home/.android-sdk-home"),
        "ANDROID_AVD_HOME": str(private / "home/.android/avd"),
        "PUB_CACHE": str(private / "pub"),
        "TMPDIR": str(private / "tmp"),
        "FLUTTER_SUPPRESS_ANALYTICS": "true",
        "CI": "true",
        "FLUTTER_TOOL_ARGS": tool_args,
    })
    env.pop("LD_PRELOAD", None)
    version_result = _run([str(dart), "--version"], env=env, timeout=60)
    version = (version_result.stderr or version_result.stdout).strip()
    if "Dart SDK version" not in version:
        raise RepackError(f"archived Dart compiler returned no valid version identity: {version!r}")
    snapshot.unlink(missing_ok=True)
    compile_args = [
        str(dart), "--verbosity=error", f"--snapshot={snapshot}", "--snapshot-kind=kernel",
        f"--packages={config}", "--no-enable-mirrors", str(entry), "--version",
    ]
    if tool_args:
        compile_args[1:1] = tool_args.split()
    _run(compile_args, cwd=root, env=env, timeout=240)
    if not snapshot.is_file() or snapshot.stat().st_size < 8 or snapshot.read_bytes()[:4] != b"\x90\xab\xcd\xef":
        raise RepackError("native Dart compiler did not produce a nonempty kernel snapshot")
    revision = _run([shutil.which("git") or "git", "-C", str(root), "rev-parse", "HEAD"], env=env).stdout.strip()
    stamp.write_text(f"{revision}:{tool_args}", encoding="utf-8")
    stamp.chmod(0o644)
    before_load_sha = sha256_file(snapshot)
    launcher = root / "bin/flutter"
    result = _run([str(launcher), "--no-version-check", "--suppress-analytics", "--version"],
                  cwd=root, env=env, timeout=180)
    if "Flutter" not in (result.stdout + result.stderr):
        raise RepackError("Flutter launcher did not load the compiled FlutterTools kernel successfully")
    if sha256_file(snapshot) != before_load_sha:
        raise RepackError("Flutter launcher rebuilt or replaced the just-compiled FlutterTools kernel")
    return version, snapshot.stat().st_size


def _validate_deb(path: Path, before_root: Path, temporary: Path,
                  postinst_path: Path | None = None) -> dict[str, str]:
    import build

    try:
        build.validate_deb_artifacts(path)
    except Exception as exc:
        raise RepackError(f"finished package failed mandatory artifact validation: {exc}") from exc
    control_fields = read_deb_identity(path)
    if control_fields != ("flutter", PACKAGE_VERSION, "aarch64"):
        raise RepackError(f"finished package has unexpected Debian identity: {control_fields!r}")
    extracted = temporary / "verify-payload"
    _run(["dpkg-deb", "-x", str(path), str(extracted)])
    after_root = extracted / FLUTTER_RELATIVE
    assert_native_inventory_equal(before_root, after_root)
    readelf = shutil.which("readelf")
    if not readelf:
        raise RepackError("binutils readelf is required for final native ELF validation")
    protected = (
        "bin/cache/dart-sdk/bin/dart",
        "bin/cache/dart-sdk/bin/dartvm",
        "bin/cache/dart-sdk/bin/dartaotruntime",
        "bin/cache/artifacts/engine/android-arm64-release/linux-arm64/gen_snapshot",
        "bin/cache/artifacts/engine/android-arm64-profile/linux-arm64/gen_snapshot",
    )
    for relative in protected:
        executable = after_root / relative
        result = _run([readelf, "-h", "-l", str(executable)], timeout=30)
        if "AArch64" not in result.stdout or "/system/bin/linker64" not in result.stdout:
            raise RepackError(f"readelf did not confirm Android ARM64 ABI for {relative}")
    control_bytes = None
    for name, member in build._ar_members(path):
        if name.startswith("control.tar"):
            control_bytes = member
            break
    if control_bytes is None:
        raise RepackError("finished package has no control archive")
    import io
    import tarfile
    try:
        with tarfile.open(fileobj=io.BytesIO(control_bytes), mode="r:*") as control_tar:
            postinst = control_tar.getmember("postinst")
            script = control_tar.extractfile(postinst)
            if script is None or not postinst.mode & 0o111:
                raise RepackError("finished package postinst is missing or not executable")
            expected = Path(postinst_path or REPO_ROOT / "scripts/install/postinst.sh").read_bytes()
            if script.read() != expected:
                raise RepackError("finished package postinst differs from the checked-in installer")
    except (KeyError, tarfile.TarError) as exc:
        raise RepackError(f"finished package control archive is invalid: {exc}") from exc
    return native_inventory(after_root)


def read_deb_identity(path: Path) -> tuple[str, str, str]:
    """Query control identity separately; dpkg-deb labels a multi-field response."""
    values = tuple(_run(["dpkg-deb", "-f", str(path), field]).stdout.strip()
                   for field in ("Package", "Version", "Architecture"))
    if any(not value or "\n" in value or "\r" in value for value in values):
        raise RepackError(f"finished package has malformed Debian identity fields: {values!r}")
    return values


def _validate_scratch_locations(temporary_root: Path, output: Path, repo_root: Path) -> None:
    """Keep recursive cleanup away from HOME/SDK and publication away from live SDKs."""
    home = Path(os.environ.get("HOME", str(Path.home()))).resolve()
    prefix = Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr")).resolve()
    protected = [home, prefix / "opt/flutter", prefix / "opt/android-sdk", home / "android-sdk"]
    for key in ("ANDROID_SDK", "ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(key):
            protected.append(Path(os.environ[key]).resolve())
    temporary_root = Path(temporary_root).resolve()
    output = Path(output).resolve(strict=False)
    for root in protected:
        try:
            temporary_root.relative_to(root)
        except ValueError:
            pass
        else:
            raise RepackError(f"temporary workspace must not be inside HOME or an installed SDK: {temporary_root}")
    for root in protected[1:]:
        try:
            output.relative_to(root)
        except ValueError:
            pass
        else:
            raise RepackError(f"output must not be inside an installed SDK: {output}")
    try:
        output.relative_to(Path(repo_root).resolve())
    except ValueError as exc:
        raise RepackError("output must remain inside this repository's install-ready artifacts") from exc


def _rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically rename within one filesystem without replacing any destination type."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        raise RepackError("atomic no-overwrite publication requires libc renameat2") from exc
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                          ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = renameat2(AT_FDCWD, os.fsencode(source), AT_FDCWD,
                       os.fsencode(destination), RENAME_NOREPLACE)
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(error, os.strerror(error), str(destination))
    if error in (errno.ENOSYS, errno.EINVAL, getattr(errno, "EOPNOTSUPP", errno.ENOSYS)):
        raise RepackError("atomic no-overwrite publication is unsupported by this kernel/filesystem")
    raise OSError(error, os.strerror(error), str(destination))


def preflight_atomic_publisher(directory: Path) -> None:
    """Prove renameat2 no-replace behavior on the destination filesystem before build work."""
    directory = Path(directory)
    try:
        with tempfile.TemporaryDirectory(prefix=".flutter-publish-probe-", dir=directory) as name:
            root = Path(name)
            source, destination = root / "candidate", root / "published"
            source.write_bytes(b"atomic publisher preflight\n")
            source.chmod(0o640)
            _rename_noreplace(source, destination)
            if (source.exists() or destination.read_bytes() != b"atomic publisher preflight\n"
                    or stat.S_IMODE(destination.stat().st_mode) != 0o640):
                raise RepackError("atomic no-overwrite publisher failed its same-filesystem probe")
            contender = root / "contender"
            contender.write_bytes(b"must not replace existing destination")
            try:
                _rename_noreplace(contender, destination)
            except FileExistsError:
                pass
            else:
                raise RepackError("atomic publisher replaced an existing destination")
            if (not contender.is_file() or destination.read_bytes() != b"atomic publisher preflight\n"):
                raise RepackError("atomic publisher changed files during collision preflight")
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except RepackError:
        raise
    except (OSError, AttributeError) as exc:
        raise RepackError(f"atomic no-overwrite publisher preflight failed: {exc}") from exc


def publish_verified(candidate: Path, output: Path, validator) -> None:
    """Validate first, then create a no-overwrite atomic publication on output's filesystem."""
    candidate, output = Path(candidate), Path(output)
    validator(candidate)
    output.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(output):
        raise RepackError(f"output already exists; refusing to overwrite: {output}")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".pending", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as target, candidate.open("rb") as source:
            shutil.copyfileobj(source, target, 1024 * 1024)
            os.fchmod(target.fileno(), stat.S_IMODE(candidate.stat().st_mode))
            target.flush()
            os.fsync(target.fileno())
        _rename_noreplace(temporary, output)
        directory_fd = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def repack(source: Path, output: Path, *, engine_stamp_json: Path | None = None,
           repo_root: Path | None = None, tool_args: str | None = None) -> Path:
    source, output = validate_paths(source, output)
    source_sha = verify_source(source)
    archived_source_commit = _verify_source_metadata(source, source_sha)
    repo_root = Path(repo_root or REPO_ROOT).resolve()
    if engine_stamp_json is None:
        prefix = Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"))
        engine_stamp_json = prefix / "opt/flutter/bin/cache/engine_stamp.json"
    engine_stamp_json = Path(engine_stamp_json).resolve()
    if tool_args is None:
        tool_args = os.environ.get("FLUTTER_TOOL_ARGS", "")

    temporary_root = Path(os.environ.get("TMPDIR", tempfile.gettempdir()))
    _validate_scratch_locations(temporary_root, output, repo_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_root.mkdir(parents=True, exist_ok=True)
    preflight_atomic_publisher(output.parent)
    with tempfile.TemporaryDirectory(prefix="flutter-verified-repack-", dir=temporary_root) as temp_name:
        temporary = Path(temp_name)
        payload, control = temporary / "payload", temporary / "control"
        payload.mkdir()
        control.mkdir()
        _extract_archive(source, payload, control)
        root = payload / FLUTTER_RELATIVE
        if not root.is_dir() or root.is_symlink():
            raise RepackError("pinned archive does not contain the expected Termux Flutter SDK root")
        engine_version = root / "bin/internal/engine.version"
        cache = root / "bin/cache"
        engine_stamp = cache / "engine_stamp.stamp"
        revision = validate_engine_metadata(engine_version, engine_stamp, engine_stamp_json)
        if (cache / "engine.stamp").read_text(encoding="utf-8").strip() != revision:
            raise RepackError("engine.stamp does not match the archived engine.version")
        package_config = root / "packages/flutter_tools/.dart_tool/package_config.json"
        tools = root / "packages/flutter_tools"
        pubspec, lock = tools / "pubspec.yaml", tools / "pubspec.lock"
        if not pubspec.is_file() or not lock.is_file() or pubspec.stat().st_mtime_ns > lock.stat().st_mtime_ns:
            raise RepackError("archived FlutterTools pubspec.lock is missing or stale; refusing network resolution")
        package_roots = normalize_package_config(root, package_config)
        _apply_required_patches(root, repo_root / "scripts/install/post_install.sh", temporary / "patch")

        try:
            import yaml
            package_yaml = yaml.safe_load((repo_root / "package.yaml").read_text(encoding="utf-8"))
        except Exception as exc:
            raise RepackError(f"current package resources are missing or invalid: {exc}") from exc
        pkg = _package_object(root, repo_root, package_yaml)
        _apply_overlay(payload, list(pkg.gen_resource("version_json")))
        engine_json_target = cache / "engine_stamp.json"
        shutil.copyfile(engine_stamp_json, engine_json_target)
        engine_json_target.chmod(0o644)
        if (cache / "engine_stamp.stamp").read_text(encoding="utf-8").strip() != revision:
            raise RepackError("engine cache stamp changed unexpectedly before compilation")

        synthetic_head = _synthetic_git(root, private=temporary / "git")
        if tool_args and any(char in tool_args for char in "\x00\n\r"):
            raise RepackError("FLUTTER_TOOL_ARGS contains an unsupported control character")
        (cache / "flutter_tools.stamp").write_text(f"{synthetic_head}:{tool_args}", encoding="utf-8")
        old_native = native_inventory(root)
        validate_package_native_inventory(pkg, root, old_native)
        dart_version, snapshot_size = _compile_and_load(root, tool_args, temporary / "runtime")
        snapshot = cache / "flutter_tools.snapshot"
        snapshot_sha = sha256_file(snapshot)
        payload_manifest = validate_package_payload_routes(pkg, root, package_roots)

        try:
            source_metadata = json.loads(source.with_name("build_metadata.json").read_text(encoding="utf-8"))
            workspace = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"], text=True,
                                       capture_output=True, check=True).stdout.strip()
        except Exception as exc:
            raise RepackError(f"cannot record source provenance: {exc}") from exc
        provenance = {
            "format": "termux-flutter-verified-payload-repack/v1",
            "flutter_version": FLUTTER_VERSION,
            "package_version": PACKAGE_VERSION,
            "architecture": "aarch64",
            "source_archive_sha256": source_sha,
            "archived_build_commit": archived_source_commit,
            "repack_workspace_commit": workspace,
            "engine_revision": revision,
            "engine_reused": True,
            "engine_metadata_sha256": sha256_file(engine_json_target),
            "dart_compiler": dart_version,
            "flutter_tools_kernel_sha256": snapshot_sha,
            "flutter_tools_kernel_bytes": snapshot_size,
            "synthetic_flutter_git_head": synthetic_head,
            "tool_args": tool_args,
            "source_build_run_id": source_metadata.get("run_id"),
            "native_elf_inventory": old_native,
            "required_payload_manifest": payload_manifest,
            "limitations": [
                "Engine/native ELF payload is reused from the pinned CI archive; this is not a fresh GN build.",
                "Android SDK binaries are not redistributed; package configuration requires a complete Termux SDK.",
            ],
        }
        write_provenance(cache / "termux_repack_provenance.json", provenance)

        # Package the extracted verified runtime with only current, explicit overlays.
        candidate = temporary / "candidate.deb"
        pkg.debuild(candidate, section=list(PUBLISHED_RESOURCES))

        # Verify the candidate before any destination path can become visible.
        postinst_source = repo_root / "scripts/install/postinst.sh"
        verified_inventory = _validate_deb(candidate, root, temporary, postinst_source)
        if verified_inventory != old_native:
            raise RepackError("finished package native inventory differs from the pinned extracted payload")
        publish_verified(candidate, output, lambda candidate_path: _validate_deb(
            candidate_path, root, temporary, postinst_source))
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.home() / "termux-flutter/flutter-termux-3.47.2-arm64/flutter_3.47.2_aarch64.deb")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[2] / "artifacts/install-ready-r4/flutter_3.47.2_aarch64.deb")
    parser.add_argument("--engine-stamp-json", type=Path, help="read-only engine metadata whose revision must match the archived engine.version")
    parser.add_argument("--tool-args", default=None, help="FlutterTools compile key arguments; defaults to FLUTTER_TOOL_ARGS or empty")
    args = parser.parse_args(argv)
    try:
        result = repack(args.source, args.output, engine_stamp_json=args.engine_stamp_json, tool_args=args.tool_args)
    except RepackError as exc:
        parser.error(str(exc))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
