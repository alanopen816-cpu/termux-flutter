import json
import hashlib
import os
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.install import configure_android


def native_aapt2(path: Path):
    """Create a minimal executable ELF64/AArch64 fixture with Termux's linker."""
    interpreter = b"/system/bin/linker64\0"
    phoff = 64
    phentsize = 56
    phnum = 1
    offset = phoff + phentsize
    header = bytearray(64)
    header[:16] = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    struct.pack_into("<HHIQQQIHHHHHH", header, 16, 2, 183, 1, 0, phoff, 0, 0, 64, phentsize, phnum, 0, 0, 0)
    program = struct.pack("<IIQQQQQQ", 3, 4, offset, 0, 0, len(interpreter), len(interpreter), 1)
    path.write_bytes(header + program + interpreter)
    path.chmod(0o755)


@pytest.fixture
def native_context(tmp_path, monkeypatch):
    prefix = tmp_path / "prefix"
    home = tmp_path / "home"
    gradle_home = tmp_path / "custom-gradle-home"
    (prefix / "bin").mkdir(parents=True)
    home.mkdir()
    gradle_home.mkdir()
    native_aapt2(prefix / "bin" / "aapt2")
    monkeypatch.setattr(configure_android, "is_termux_runtime", lambda _prefix: True)
    return prefix, home, gradle_home


def test_adds_global_native_aapt2_and_canonical_sdk_preserving_settings(native_context):
    prefix, home, gradle_home = native_context
    settings = home / ".flutter_settings"
    settings.write_text('{"analytics":false,"unrelated":"preserve"}\n', encoding="utf-8")
    props = gradle_home / "gradle.properties"
    props.write_text("org.gradle.jvmargs=-Xmx2g\n", encoding="utf-8")

    configure_android.configure(prefix, home, prefix / "opt/android-sdk", gradle_home)

    assert json.loads(settings.read_text()) == {
        "analytics": False,
        "unrelated": "preserve",
        "android-sdk": str(prefix / "opt/android-sdk"),
    }
    assert props.read_text() == (
        "org.gradle.jvmargs=-Xmx2g\n"
        f"android.aapt2FromMavenOverride={prefix}/bin/aapt2\n"
    )


def test_keeps_custom_native_aapt2_and_idempotent_bytes(native_context):
    prefix, home, gradle_home = native_context
    custom = home / "custom-aapt2"
    native_aapt2(custom)
    props = gradle_home / "gradle.properties"
    original = f"android.aapt2FromMavenOverride={custom}\norg.gradle.daemon=false\n"
    props.write_text(original)
    settings = home / ".flutter_settings"
    settings.write_text(json.dumps({"android-sdk": str(prefix / "opt/android-sdk"), "keep": 1}) + "\n")

    configure_android.configure(prefix, home, prefix / "opt/android-sdk", gradle_home)

    assert props.read_text() == original
    assert settings.read_text() == json.dumps({"android-sdk": str(prefix / "opt/android-sdk"), "keep": 1}) + "\n"


def test_repeat_preserves_existing_modes_and_content_hashes(native_context):
    prefix, home, gradle_home = native_context
    settings = home / ".flutter_settings"
    props = gradle_home / "gradle.properties"
    settings.write_text('{"keep":true}\n', encoding="utf-8")
    props.write_text("org.gradle.daemon=false\n", encoding="utf-8")
    settings.chmod(0o640)
    props.chmod(0o604)
    configure_android.configure(prefix, home, prefix / "opt/android-sdk", gradle_home)
    before = {
        path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode & 0o777)
        for path in (settings, props)
    }

    configure_android.configure(prefix, home, prefix / "opt/android-sdk", gradle_home)

    assert {
        path: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode & 0o777)
        for path in (settings, props)
    } == before


def test_respects_explicit_gradle_user_home_environment(native_context, monkeypatch):
    prefix, home, gradle_home = native_context
    monkeypatch.setenv("GRADLE_USER_HOME", str(gradle_home))
    configure_android.configure(prefix, home, prefix / "opt/android-sdk")
    assert "android.aapt2FromMavenOverride" in (gradle_home / "gradle.properties").read_text()
    assert not (home / ".gradle/gradle.properties").exists()


@pytest.mark.parametrize(
    "property_line, custom_name",
    [
        ("android.aapt2FromMavenOverride \t {path}\n", "aapt2"),
        (
            "android.aapt2FromMavenOverrid\\u0065\t {path}\\: with\\ space\\\n    continuation\n",
            "aapt2",
        ),
    ],
)
def test_java_properties_delimiters_escapes_and_continuations_are_reused(
    native_context, property_line, custom_name
):
    prefix, home, gradle_home = native_context
    custom = home / custom_name
    if "continuation" in property_line:
        custom = home / f"{custom_name}: with spacecontinuation"
        property_path = home / custom_name
    else:
        property_path = custom
    native_aapt2(custom)
    props = gradle_home / "gradle.properties"
    original = property_line.format(path=str(property_path))
    props.write_text(original, encoding="utf-8", newline="")
    digest = hashlib.sha256(props.read_bytes()).hexdigest()

    configure_android.configure(prefix, home, prefix / "opt/android-sdk", gradle_home)

    assert hashlib.sha256(props.read_bytes()).hexdigest() == digest


def test_inserted_override_escapes_path_spaces(native_context):
    prefix, home, gradle_home = native_context
    moved_prefix = prefix.parent / "Termux Prefix"
    moved_prefix.mkdir()
    # configure uses PREFIX/bin/aapt2, so create the same layout under a spaced PREFIX.
    (moved_prefix / "bin").mkdir()
    native_aapt2(moved_prefix / "bin" / "aapt2")
    configure_android.configure(moved_prefix, home, moved_prefix / "opt/android-sdk", gradle_home)
    escaped_path = str(moved_prefix / "bin/aapt2").replace(" ", "\\ ")
    assert (gradle_home / "gradle.properties").read_text().endswith(
        f"android.aapt2FromMavenOverride={escaped_path}\n"
    )


def test_chown_failure_with_wrong_temp_gid_does_not_replace_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "gradle.properties"
    target.write_text("keep=old\n", encoding="utf-8")
    target.chmod(0o640)
    original_stat = target.stat()
    original = (
        hashlib.sha256(target.read_bytes()).hexdigest(),
        original_stat.st_mode & 0o777,
        original_stat.st_uid,
        original_stat.st_gid,
    )
    previous = target.stat()
    actual_stat = os.stat

    def denied_chown(*_args):
        raise PermissionError("simulated ownership denial")

    def mismatched_temp_stat(path, *args, **kwargs):
        current = actual_stat(path, *args, **kwargs)
        if Path(path).name.startswith(f".{target.name}."):
            return SimpleNamespace(st_uid=previous.st_uid, st_gid=previous.st_gid + 1)
        return current

    monkeypatch.setattr(configure_android.os, "chown", denied_chown)
    monkeypatch.setattr(configure_android.os, "stat", mismatched_temp_stat)
    with pytest.raises(PermissionError, match="ownership"):
        configure_android._atomic_write(target, b"keep=new\n", previous)
    current_stat = target.stat()
    assert (
        hashlib.sha256(target.read_bytes()).hexdigest(),
        current_stat.st_mode & 0o777,
        current_stat.st_uid,
        current_stat.st_gid,
    ) == original


def test_invalid_custom_override_fails_without_erasing_user_config(native_context):
    prefix, home, gradle_home = native_context
    props = gradle_home / "gradle.properties"
    original = "android.aapt2FromMavenOverride=/not/native/aapt2\nkeep=this\n"
    props.write_text(original)
    with pytest.raises(configure_android.ConfigurationError, match="not a native Termux AAPT2"):
        configure_android.configure(prefix, home, prefix / "opt/android-sdk", gradle_home)
    assert props.read_text() == original


def test_malformed_flutter_settings_fails_before_gradle_write(native_context):
    prefix, home, gradle_home = native_context
    settings = home / ".flutter_settings"
    settings.write_text("{broken", encoding="utf-8")
    props = gradle_home / "gradle.properties"
    with pytest.raises(configure_android.ConfigurationError, match="invalid JSON"):
        configure_android.configure(prefix, home, prefix / "opt/android-sdk", gradle_home)
    assert not props.exists()


def test_rejects_non_native_aapt2_before_changing_settings(native_context):
    prefix, home, gradle_home = native_context
    settings = home / ".flutter_settings"
    settings.write_text('{"keep":true}\n', encoding="utf-8")
    original = settings.read_bytes()
    # An executable script (or x86_64 binary) is not the Termux AArch64 ELF.
    aapt2 = prefix / "bin/aapt2"
    aapt2.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    aapt2.chmod(0o755)
    with pytest.raises(configure_android.ConfigurationError, match="not a native Termux AAPT2"):
        configure_android.configure(prefix, home, prefix / "opt/android-sdk", gradle_home)
    assert settings.read_bytes() == original
    assert not (gradle_home / "gradle.properties").exists()


def test_non_termux_runtime_does_not_persist_configuration(tmp_path, monkeypatch):
    prefix = tmp_path / "prefix"
    home = tmp_path / "home"
    gradle_home = tmp_path / "gradle"
    prefix.mkdir()
    home.mkdir()
    monkeypatch.setattr(configure_android, "is_termux_runtime", lambda _prefix: False)
    configure_android.configure(prefix, home, prefix / "opt/android-sdk", gradle_home)
    assert not (home / ".flutter_settings").exists()
    assert not (gradle_home / "gradle.properties").exists()
