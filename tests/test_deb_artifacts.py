import io
import json
import struct
import tarfile
from pathlib import Path

import pytest

import package
import build
from build import _ar_members


def _write_android_elf(path):
    interpreter = b"/system/bin/linker64\0"
    header_size, ph_size = 64, 56
    ident = b"\x7fELF" + bytes((2, 1, 1, 0)) + bytes(8)
    header = struct.pack("<16sHHIQQQIHHHHHH", ident, 3, 183, 1, 0,
                         header_size, 0, 0, header_size, ph_size, 1, 0, 0, 0)
    ph = struct.pack("<IIQQQQQQ", 3, 4, header_size + ph_size, 0, 0,
                     len(interpreter), len(interpreter), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + ph + interpreter)
    path.chmod(0o755)


def _make_valid_prefixed_deb(tmp_path, *, add_bare_package_alias=False, escape_package_root=False):
    root = tmp_path / "package"
    prefix = root / "data/data/com.termux/files/usr"
    sdk = prefix / "opt/flutter"
    control = root / "DEBIAN"
    control.mkdir(parents=True)
    control.chmod(0o755)
    postinst = tmp_path / "postinst"
    postinst.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    postinst.chmod(0o755)

    for relative in build.REQUIRED_DEB_EXECUTABLE_ARTIFACTS:
        _write_android_elf(sdk / relative.removeprefix("opt/flutter/"))
    for relative in build.REQUIRED_DEB_TOOL_ARTIFACTS:
        path = sdk / relative.removeprefix("opt/flutter/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    revision = "a" * 40
    (sdk / "bin/internal/engine.version").write_text(revision)
    (sdk / "bin/cache/engine_stamp.stamp").write_text(revision)
    (sdk / "bin/cache/engine_stamp.json").write_text(json.dumps({"git_revision": revision}))
    (sdk / "bin/cache/flutter_tools.snapshot").write_bytes(b"\x90\xab\xcd\xefkernel")
    (sdk / "bin/cache/flutter_tools.stamp").write_text("b" * 40 + ":")
    (sdk / ".git/HEAD").parent.mkdir(parents=True, exist_ok=True)
    (sdk / ".git/HEAD").write_text("ref: refs/heads/stable\n")
    (sdk / ".git/refs/heads/stable").parent.mkdir(parents=True, exist_ok=True)
    (sdk / ".git/refs/heads/stable").write_text("b" * 40 + "\n")
    (sdk / ".git/termux_synthetic").write_text("fixture")
    (sdk / "bin/cache/dart-sdk/bin/resources/devtools/version.json").write_text(
        '{"version":"2.60.0"}')
    package_root = sdk / ".pub-cache/hosted/pub.dev/example-1.0.0"
    package_root.mkdir(parents=True)
    (package_root / "lib.dart").write_text("// fixture")
    config = sdk / "packages/flutter_tools/.dart_tool/package_config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"configVersion": 2, "packages": [
        {"name": "example", "rootUri": ("%2e%2e/%2e%2e/%2e%2e/%2e%2e/outside/" if escape_package_root
                                                  else "../../../.pub-cache/hosted/pub.dev/example-1.0.0/")}
    ]}))
    for relative in build.REQUIRED_DEB_INSTALLER_ARTIFACTS:
        script = prefix / relative
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
    alias = None
    if add_bare_package_alias:
        alias = root / "opt/flutter/.pub-cache/hosted/pub.dev/example-1.0.0/lib.dart"
        alias.parent.mkdir(parents=True)
        alias.write_text("// ambiguous duplicate")
    deb = tmp_path / "fixture.deb"
    pkg = package.Package(
        root=tmp_path, arch="arm64", control={
            "Package": "flutter", "Version": "3.47.2-1", "Architecture": "aarch64",
            "Maintainer": "test", "Description": "test",
        }, resource={
            "fixture": {"source": str(root / "data"), "output": "data", "mode": "0755"},
            "engine": {
                "source": str(sdk / "bin/cache/artifacts/engine"),
                "output": "data/data/com.termux/files/usr/opt/flutter/bin/cache/artifacts/engine",
            },
            "git": {
                "source": str(sdk / ".git"),
                "output": "data/data/com.termux/files/usr/opt/flutter/.git",
            },
            **({"alias": {"source": str(alias), "output": "opt/flutter/.pub-cache/hosted/pub.dev/example-1.0.0/lib.dart"}}
               if alias is not None else {}),
        },
        tag="3.47.2", revision="1", maintainer_scripts={"postinst": postinst},
    )
    pkg.debuild(deb, section=["fixture", "engine", "git", *( ["alias"] if alias is not None else [])])
    return deb


def test_validator_resolves_package_roots_from_installed_termux_prefix(tmp_path):
    deb = _make_valid_prefixed_deb(tmp_path)

    assert build.validate_deb_artifacts(deb) is None


def test_validator_rejects_ambiguous_duplicate_sdk_package_paths(tmp_path):
    deb = _make_valid_prefixed_deb(tmp_path, add_bare_package_alias=True)

    with pytest.raises(RuntimeError, match="ambiguous duplicate archive path"):
        build.validate_deb_artifacts(deb)


def test_validator_rejects_percent_encoded_package_root_escape(tmp_path):
    deb = _make_valid_prefixed_deb(tmp_path, escape_package_root=True)

    with pytest.raises(RuntimeError, match="escapes the packaged Flutter SDK"):
        build.validate_deb_artifacts(deb)


def test_deb_payload_path_normalization_is_idempotent_and_rejects_escapes():
    logical = "opt/flutter/.pub-cache/hosted/pub.dev/example-1.0.0"
    prefixed = "./data/data/com.termux/files/usr/" + logical

    assert build._normalize_deb_payload_path(prefixed) == logical
    assert build._normalize_deb_payload_path(logical) == logical
    assert build._normalize_deb_payload_path(build._normalize_deb_payload_path(prefixed)) == logical
    with pytest.raises(ValueError, match="unsafe path"):
        build._normalize_deb_payload_path("../opt/flutter/outside")
    with pytest.raises(ValueError, match="unsafe path"):
        build._normalize_deb_payload_path("/opt/flutter/outside")


def test_deb_control_archive_contains_executable_postinst(tmp_path):
    root = tmp_path / "flutter"
    root.mkdir()
    postinst = tmp_path / "postinst.sh"
    postinst.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    postinst.chmod(0o755)
    control = {
        "Package": "flutter",
        "Version": "3.47.2-1",
        "Architecture": "aarch64",
        "Maintainer": "Termux Flutter <test@example.com>",
        "Depends": "bash, python, git, aapt, aapt2",
        "Description": "Flutter for Termux",
    }
    pkg = package.Package(
        root=root, arch="arm64", control=control, resource={}, tag="3.47.2",
        revision="1", maintainer_scripts={"postinst": postinst},
    )
    output = tmp_path / "flutter.deb"
    pkg.debuild(output)

    control_archive = next(data for name, data in _ar_members(output) if name.startswith("control.tar"))
    with tarfile.open(fileobj=io.BytesIO(control_archive), mode="r:*") as archive:
        script = archive.getmember("postinst")
        assert script.mode & 0o111
        assert archive.extractfile(script).read() == postinst.read_bytes()
        control_text = archive.extractfile(archive.getmember("control")).read().decode()
    assert "Version: 3.47.2-1\n" in control_text
    assert "Depends: bash, python, git, aapt, aapt2\n" in control_text


@pytest.mark.parametrize("script_name", ["preinst", "postrm", "prerm"])
def test_only_postinst_maintainer_script_is_supported(tmp_path, script_name):
    root = tmp_path / "flutter"
    root.mkdir()
    script = tmp_path / "script"
    script.write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises(ValueError, match="unsupported maintainer script"):
        package.Package(
            root=root, arch="arm64", control={
                "Package": "flutter", "Version": "3.47.2-1", "Architecture": "aarch64",
                "Maintainer": "test", "Description": "test",
            }, resource={}, maintainer_scripts={script_name: script},
        )


def test_symlinked_postinst_is_rejected(tmp_path):
    root = tmp_path / "flutter"
    root.mkdir()
    script = tmp_path / "actual-postinst"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    link = tmp_path / "postinst-link"
    link.symlink_to(script)
    pkg = package.Package(
        root=root, arch="arm64", control={
            "Package": "flutter", "Version": "3.47.2-1", "Architecture": "aarch64",
            "Maintainer": "test", "Description": "test",
        }, resource={}, maintainer_scripts={"postinst": link},
    )
    with pytest.raises(FileNotFoundError, match="unsafe maintainer script"):
        list(pkg.gen_control_files())
