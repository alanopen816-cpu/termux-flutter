import json
import struct
import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import build
from build import (
    Build,
    REQUIRED_DEB_ARTIFACTS,
    REQUIRED_DEB_EXECUTABLE_ARTIFACTS,
    normalize_flutter_tools_package_config,
    validate_android_gen_snapshot,
)


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


def _empty_build(root, toolchain, sysroot):
    builder = Build()
    builder.root = Path(root)
    builder.toolchain = str(toolchain)
    builder.jobs = 1
    builder._sysroot = SimpleNamespace(path=str(sysroot))
    builder._validate_ndk = lambda _toolchain: None
    return builder


def test_configure_android_passes_separate_termux_host_settings_and_android_target(tmp_path, monkeypatch):
    root = tmp_path / "flutter"
    toolchain = tmp_path / "ndk" / "toolchains" / "llvm" / "prebuilt" / "linux-x86_64"
    sysroot = tmp_path / "termux-sysroot"
    toolchain.mkdir(parents=True)
    sysroot.mkdir()
    calls = []
    monkeypatch.setattr(build.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)))

    builder = _empty_build(root, toolchain, sysroot)
    builder.configure_android(mode="release")

    cmd = calls[0][0]
    joined = " ".join(cmd)
    assert "--android" in cmd and "--android-cpu" in cmd
    assert 'termux_cross_host=true' in joined
    assert f'termux_host_toolchain="{toolchain}"' in joined
    assert f'termux_host_sysroot="{sysroot}"' in joined
    assert "--android-cpu arm64" in joined


def test_non_cross_host_toolchain_keeps_legacy_custom_toolchain_contract():
    patch = Path("patches/3.47.2/engine.patch").read_text()
    assert 'assert(defined(custom_toolchain) && custom_toolchain != "")' in patch
    assert 'prefix = "$custom_toolchain/bin"' in patch
    assert 'if (termux_cross_host)' in patch


def test_build_android_gen_snapshot_uses_gn_outputs_for_ninja_target(tmp_path, monkeypatch):
    root = tmp_path / "flutter"
    out = root / "engine/src/out/android_release_arm64"
    relative_output = "target/host/gen_snapshot"
    output = out / relative_output
    output.parent.mkdir(parents=True)
    calls = []
    working_dirs = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        working_dirs.append(kwargs.get("cwd"))
        if "outputs" in cmd:
            return SimpleNamespace(stdout=relative_output + "\n")
        if cmd[0] == "ninja":
            _write_elf(output)

    monkeypatch.setattr(build.subprocess, "run", run)
    builder = _empty_build(root, tmp_path / "ndk", tmp_path / "sysroot")

    canonical = Path(builder.build_android_gen_snapshot(mode="release"))

    expected_gn = root.resolve() / "engine/src/flutter/third_party/gn/gn"
    assert Path(calls[0][0]) == expected_gn
    assert calls[0][1:] == ["outputs", str(out.resolve()), (
        "//flutter/third_party/dart/runtime/bin:gen_snapshot"
        "(//build/toolchain/termux:arm64)")]
    assert calls[0][1] == "outputs"
    assert working_dirs[0] == root.resolve() / "engine/src"
    assert calls[1][0] == "ninja"
    assert calls[1][3] == relative_output
    assert canonical.exists()
    validate_android_gen_snapshot(canonical)


def test_x64_gen_snapshot_output_is_rejected_without_copy(tmp_path, monkeypatch):
    root = tmp_path / "flutter"
    out = root / "engine/src/out/android_release_arm64"
    relative_output = "clang_arm64/exe.stripped/gen_snapshot"
    output = out / relative_output
    output.parent.mkdir(parents=True)
    monkeypatch.setattr(
        build.subprocess, "run",
        lambda cmd, **kwargs: (
            SimpleNamespace(stdout=relative_output + "\n") if "outputs" in cmd
            else _write_elf(output, machine=62)),
    )
    builder = _empty_build(root, tmp_path / "ndk", tmp_path / "sysroot")

    with pytest.raises(ValueError, match="AArch64"):
        builder.build_android_gen_snapshot(mode="release")
    assert not (out / "clang_arm64/gen_snapshot").exists()


def test_valid_canonical_output_is_not_copied_onto_itself(tmp_path, monkeypatch):
    root = tmp_path / "flutter"
    out = root / "engine/src/out/android_release_arm64"
    relative_output = "clang_arm64/gen_snapshot"
    output = out / relative_output
    output.parent.mkdir(parents=True)
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if "outputs" in cmd:
            return SimpleNamespace(stdout=relative_output + "\n")
        _write_elf(output)

    monkeypatch.setattr(build.subprocess, "run", run)
    builder = _empty_build(root, tmp_path / "ndk", tmp_path / "sysroot")
    result = Path(builder.build_android_gen_snapshot(mode="release"))

    assert result == output
    assert result.exists()
    assert len(calls) == 2


def test_required_deb_artifacts_include_release_and_profile_generators():
    assert any("android-arm64-release" in path and path.endswith("/gen_snapshot")
               for path in REQUIRED_DEB_ARTIFACTS)


def _stub_deb(monkeypatch, tmp_path, *, missing=None, invalid=None, non_executable=None,
              invalid_native=None, invalid_snapshot=False, invalid_devtools=False, package_uri=None, has_postinst=True,
              postinst_mode=0o755):
    archive = io.BytesIO()
    revision = "e" * 40
    payloads = {
        "opt/flutter/packages/flutter_tools/bin/flutter_tools.dart": b"void main() {}\n",
        "opt/flutter/packages/flutter_tools/pubspec.yaml": b"name: flutter_tools\n",
        "opt/flutter/packages/flutter_tools/pubspec.lock": b"# resolved\n",
        "opt/flutter/packages/flutter_tools/.dart_tool/package_config.json": json.dumps({
            "configVersion": 2, "packages": ([] if package_uri is None else [{"name": "x", "rootUri": package_uri}]),
        }).encode(),
        "opt/flutter/bin/cache/flutter_tools.snapshot": b"\x90\xab\xcd\xef\x01\x00\x00\x00kernel bytes",
        "opt/flutter/bin/cache/flutter_tools.stamp": f"{revision}:".encode(),
        "opt/flutter/bin/cache/engine_stamp.json": json.dumps({
            "build_time_ms": 1, "git_revision": "engine", "git_revision_date": "2026-01-01T00:00:00Z",
            "content_hash": "engine",
        }).encode(),
        "opt/flutter/bin/cache/engine_stamp.stamp": b"engine",
        "opt/flutter/bin/internal/engine.version": b"engine",
        "opt/flutter/bin/cache/dart-sdk/bin/resources/devtools/version.json": b'{"app_name":"devtools_app","version":"2.60.0","package_name":"devtools_app"}',
        "opt/flutter/.git/HEAD": b"ref: refs/heads/stable\n",
        "opt/flutter/.git/refs/heads/stable": revision.encode(),
        "opt/flutter/.git/termux_synthetic": b"synthetic-v1\n",
        "share/flutter/post_install.sh": b"#!/bin/bash\nexit 0\n",
        "share/flutter/configure_android.py": b"#!/usr/bin/env python3\n",
        "share/flutter/flutter_package_bootstrap.py": b"#!/usr/bin/env python3\n",
    }
    for suffix in REQUIRED_DEB_EXECUTABLE_ARTIFACTS[:3]:
        if invalid_native != suffix:
            source = _write_elf(tmp_path / Path(suffix).name)
            payloads[suffix] = source.read_bytes()
        else:
            payloads[suffix] = b"not an Android ELF"
    if invalid_snapshot:
        payloads["opt/flutter/bin/cache/flutter_tools.snapshot"] = b"not-kernel-bytes"
    if invalid_devtools:
        payloads["opt/flutter/bin/cache/dart-sdk/bin/resources/devtools/version.json"] = b'{"version":3}'
    for suffix in REQUIRED_DEB_EXECUTABLE_ARTIFACTS[-2:]:
        if not (invalid and invalid[0] == suffix):
            source = _write_elf(tmp_path / Path(suffix).parent.name)
            payloads[suffix] = source.read_bytes()
    with tarfile.open(fileobj=archive, mode="w") as data_tar:
        for suffix in REQUIRED_DEB_ARTIFACTS:
            if suffix == missing:
                continue
            data = payloads.get(suffix.removeprefix("opt/flutter/"), payloads.get(suffix, b"runtime"))
            if suffix in REQUIRED_DEB_EXECUTABLE_ARTIFACTS[-2:]:
                options = invalid[1] if invalid and invalid[0] == suffix else {}
                source = _write_elf(tmp_path / Path(suffix).parent.name, **options)
                data = source.read_bytes()
            member = tarfile.TarInfo(f"data/data/com.termux/files/usr/{suffix}")
            executable = suffix in REQUIRED_DEB_EXECUTABLE_ARTIFACTS or suffix in build.REQUIRED_DEB_INSTALLER_ARTIFACTS
            member.mode = 0o644 if suffix == non_executable else (0o755 if executable else 0o644)
            member.size = len(data)
            data_tar.addfile(member, io.BytesIO(data))
    control = io.BytesIO()
    with tarfile.open(fileobj=control, mode="w") as control_tar:
        if has_postinst:
            postinst = tarfile.TarInfo("postinst")
            postinst.mode = postinst_mode
            postinst_data = b"#!/bin/sh\nexit 0\n"
            postinst.size = len(postinst_data)
            control_tar.addfile(postinst, io.BytesIO(postinst_data))
    monkeypatch.setattr(build, "_ar_members", lambda _path: iter([
        ("data.tar", archive.getvalue()), ("control.tar", control.getvalue())
    ]))
    return tmp_path / "fixture.deb"


def test_validate_deb_artifacts_accepts_both_arm64_generators(tmp_path, monkeypatch):
    build.validate_deb_artifacts(_stub_deb(monkeypatch, tmp_path))


def test_validate_deb_artifacts_requires_profile_generator(tmp_path, monkeypatch):
    profile = next(path for path in REQUIRED_DEB_ARTIFACTS if "android-arm64-profile" in path)
    with pytest.raises(RuntimeError, match="missing required"):
        build.validate_deb_artifacts(_stub_deb(monkeypatch, tmp_path, missing=profile))


def test_validate_deb_artifacts_requires_fluttertools_stamp_and_sources(tmp_path, monkeypatch):
    required = "opt/flutter/bin/cache/flutter_tools.stamp"
    with pytest.raises(RuntimeError, match="missing required"):
        build.validate_deb_artifacts(_stub_deb(monkeypatch, tmp_path, missing=required))


def test_validate_deb_artifacts_requires_control_postinst(tmp_path, monkeypatch):
    deb = _stub_deb(monkeypatch, tmp_path)
    monkeypatch.setattr(build, "_ar_members", lambda _path: iter([("data.tar", b"")]))
    with pytest.raises(ValueError, match="control archive not found"):
        build.validate_deb_artifacts(deb)


@pytest.mark.parametrize("artifact", REQUIRED_DEB_EXECUTABLE_ARTIFACTS[:3])
def test_validate_deb_artifacts_requires_native_dart_executables(tmp_path, monkeypatch, artifact):
    with pytest.raises(ValueError, match="Android ARM64"):
        build.validate_deb_artifacts(_stub_deb(monkeypatch, tmp_path, invalid_native=artifact))


def test_validate_deb_artifacts_requires_kernel_snapshot(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="kernel snapshot"):
        build.validate_deb_artifacts(_stub_deb(monkeypatch, tmp_path, invalid_snapshot=True))


def test_validate_deb_artifacts_requires_valid_devtools_metadata(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="DevTools version metadata"):
        build.validate_deb_artifacts(_stub_deb(monkeypatch, tmp_path, invalid_devtools=True))


@pytest.mark.parametrize("artifact", build.REQUIRED_DEB_INSTALLER_ARTIFACTS)
def test_validate_deb_artifacts_requires_installer_helpers(tmp_path, monkeypatch, artifact):
    with pytest.raises(RuntimeError, match="missing required"):
        build.validate_deb_artifacts(_stub_deb(monkeypatch, tmp_path, missing=artifact))


def test_validate_deb_artifacts_rejects_encoded_package_root_escape(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="escapes the packaged Flutter SDK"):
        build.validate_deb_artifacts(_stub_deb(
            monkeypatch, tmp_path, package_uri="%2e%2e/%2e%2e/%2e%2e/%2e%2e/outside/"))


def test_validate_deb_artifacts_requires_postinst_control_entry(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="missing executable postinst"):
        build.validate_deb_artifacts(_stub_deb(monkeypatch, tmp_path, has_postinst=False))


def test_validate_deb_artifacts_requires_executable_postinst(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="postinst is not executable"):
        build.validate_deb_artifacts(_stub_deb(monkeypatch, tmp_path, postinst_mode=0o644))


def test_flutter_tools_package_config_is_normalized_to_stable_relative_roots(tmp_path):
    tree = tmp_path / "flutter"
    config_dir = tree / "packages/flutter_tools/.dart_tool"
    package_root = tree / "packages/a package"
    config_dir.mkdir(parents=True)
    package_root.mkdir(parents=True)
    config = config_dir / "package_config.json"
    config.write_text(json.dumps({"configVersion": 2, "packages": [{
        "name": "a", "rootUri": package_root.as_uri() + "/", "packageUri": "lib/",
    }]}))
    normalize_flutter_tools_package_config(config, tree)
    normalized = json.loads(config.read_text())
    assert normalized["packages"][0]["rootUri"] == "../../a%20package/"
    before = (config.read_bytes(), config.stat().st_ino, config.stat().st_mtime_ns)
    normalize_flutter_tools_package_config(config, tree)
    assert (config.read_bytes(), config.stat().st_ino, config.stat().st_mtime_ns) == before


def test_flutter_tools_package_config_rejects_external_root(tmp_path):
    tree = tmp_path / "flutter"
    config_dir = tree / "packages/flutter_tools/.dart_tool"
    config_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    config = config_dir / "package_config.json"
    config.write_text(json.dumps({"configVersion": 2, "packages": [{
        "name": "external", "rootUri": outside.as_uri() + "/",
    }]}))
    with pytest.raises(RuntimeError, match="escapes or is missing"):
        normalize_flutter_tools_package_config(config, tree)


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
