"""Regression tests for keeping installer fixtures out of persistent user state."""

import os
import hashlib
import shutil
import subprocess
from pathlib import Path
import sys

from conftest import _installed_dartvm, isolate_test_user_state, isolated_user_env
from test_offline_cache_repair import create_mock_env, run_post_install
from test_android_configuration import native_aapt2


def test_post_install_fixture_does_not_mutate_callers_flutter_settings(tmp_path, monkeypatch):
    caller_home = tmp_path / "caller-home"
    caller_home.mkdir()
    settings = caller_home / ".flutter_settings"
    original_settings = '{\n  "analytics": true,\n  "custom-value": "preserve me"\n}\n'
    settings.write_text(original_settings)
    unrelated = caller_home / "unrelated.txt"
    unrelated.write_text("preserve caller data\n")
    monkeypatch.setenv("HOME", str(caller_home))

    flutter_root, android_sdk, prefix, _ = create_mock_env(tmp_path / "fixture")
    # The post-install helper should run its real file validation and updates in
    # this isolated fixture, while only the Android system identity is mocked.
    native_aapt2(prefix / "bin" / "aapt2")
    wrapper = prefix / "bin" / "python3"
    helper_path = Path(__file__).parent.parent / "scripts/install/configure_android.py"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(Path(__file__).parent.parent)!r})\n"
        "arguments = sys.argv[1:]\n"
        f"if arguments and Path(arguments[0]).resolve() == Path({str(helper_path.resolve())!r}):\n"
        "    from scripts.install import configure_android\n"
        "    configure_android.is_termux_runtime = lambda _prefix: True\n"
        "    sys.argv = arguments\n"
        "    raise SystemExit(configure_android.main())\n"
        "os.execv(sys.executable, [sys.executable, *arguments])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    result = run_post_install(flutter_root, android_sdk, prefix, ["--apply"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert settings.read_text() == original_settings
    assert unrelated.read_text() == "preserve caller data\n"
    assert str(android_sdk) not in settings.read_text()
    fixture_settings = prefix.parent / ".post-install-user-state" / "home" / ".flutter_settings"
    assert str(android_sdk) in fixture_settings.read_text()


def test_pytest_process_uses_test_local_user_directories(tmp_path_factory):
    test_root = tmp_path_factory.getbasetemp()
    assert Path(os.environ["HOME"]).is_relative_to(test_root)
    for variable in (
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "GRADLE_USER_HOME",
        "PUB_CACHE",
        "ANDROID_USER_HOME",
        "ANDROID_SDK_HOME",
        "PREFIX",
        "FLUTTER_ROOT",
        "ANDROID_HOME",
        "ANDROID_SDK_ROOT",
        "ANDROID_SDK",
        "ANDROID_NDK_HOME",
        "ANDROID_NDK_ROOT",
        "ANDROID_NDK",
        "NDK_ROOT",
    ):
        assert Path(os.environ[variable]).is_relative_to(test_root)
    assert Path(os.environ["TMPDIR"]).is_relative_to(test_root)
    assert Path(os.environ["TEST_USER_STATE_ROOT"]).is_relative_to(test_root)
    assert Path(os.environ["TEST_USER_GUARD_BIN"]).is_relative_to(test_root)
    assert "DART_SDK" not in os.environ, "fixture-specific FLUTTER_ROOT must determine DART_SDK"
    dartvm = os.environ.get("DARTVM")
    if dartvm:
        assert Path(dartvm).is_absolute()


def test_test_user_state_is_not_nested_in_individual_tmp_path(tmp_path):
    user_state = Path(os.environ["TEST_USER_STATE_ROOT"])
    assert not user_state.is_relative_to(tmp_path)
    assert not (tmp_path / ".pytest-user-state").exists()


def test_configured_native_dartvm_survives_user_environment_isolation(tmp_path):
    dartvm = tmp_path / "mock-termux-prefix" / "bin" / "dartvm"
    dartvm.parent.mkdir(parents=True)
    dartvm.write_text("#!/bin/sh\nexit 0\n")
    dartvm.chmod(0o755)
    original_env = {"DARTVM": str(dartvm), "PATH": "", "PREFIX": str(tmp_path / "missing-prefix")}

    discovered = _installed_dartvm(original_env)
    isolated = isolated_user_env(tmp_path / "isolated-state", base_env=original_env)

    assert discovered == str(dartvm.resolve())
    assert isolated["DARTVM"] == discovered


def test_isolated_environment_redirects_sdk_roots_and_removes_live_flutter_path(tmp_path):
    live_prefix = tmp_path / "caller-prefix"
    live_flutter = live_prefix / "opt" / "flutter"
    live_android = tmp_path / "caller-android-sdk"
    live_ndk = tmp_path / "caller-ndk"
    for path in (live_flutter / "bin", live_prefix / "bin", live_android, live_ndk):
        path.mkdir(parents=True)

    caller = {
        "PATH": f"{live_flutter / 'bin'}:{live_prefix / 'bin'}:/usr/bin",
        "PREFIX": str(live_prefix),
        "FLUTTER_ROOT": str(live_flutter),
        "ANDROID_HOME": str(live_android),
        "ANDROID_SDK_ROOT": str(live_android),
        "ANDROID_SDK": str(live_android),
        "ANDROID_NDK_HOME": str(live_ndk),
        "ANDROID_NDK_ROOT": str(live_ndk),
        "ANDROID_NDK": str(live_ndk),
        "NDK_ROOT": str(live_ndk),
        "DART_SDK": str(live_flutter / "bin" / "cache" / "dart-sdk"),
    }
    sentinels = (
        live_flutter / "bin" / "cache" / "flutter_tools.snapshot",
        live_flutter / "bin" / "cache" / "flutter_tools.stamp",
        live_flutter / "bin" / "cache" / "dart-sdk" / "bin" / "dartvm",
    )
    for sentinel in sentinels:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_bytes(b"caller SDK sentinel\n")
        sentinel.chmod(0o640)
    before = {
        path: (
            path.stat().st_mode,
            path.stat().st_uid,
            path.stat().st_gid,
            path.stat().st_mtime_ns,
            path.stat().st_ino,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sentinels
    }

    isolated = isolated_user_env(tmp_path / "isolated-state", base_env=caller)

    assert isolated["PREFIX"] != str(live_prefix)
    assert isolated["FLUTTER_ROOT"] != str(live_flutter)
    assert isolated["ANDROID_HOME"] != str(live_android)
    assert isolated["ANDROID_SDK_ROOT"] == isolated["ANDROID_HOME"]
    assert isolated["ANDROID_SDK"] == isolated["ANDROID_HOME"]
    assert isolated["ANDROID_NDK_HOME"] != str(live_ndk)
    assert isolated["ANDROID_NDK_ROOT"] == isolated["ANDROID_NDK_HOME"]
    assert isolated["ANDROID_NDK"] == isolated["ANDROID_NDK_HOME"]
    assert isolated["NDK_ROOT"] == isolated["ANDROID_NDK_HOME"]
    assert "DART_SDK" not in isolated, "caller DART_SDK must not override a fixture FLUTTER_ROOT"
    assert str(live_flutter / "bin") not in isolated["PATH"].split(os.pathsep)
    assert str(live_prefix / "bin") in isolated["PATH"].split(os.pathsep)
    after = {
        path: (
            path.stat().st_mode,
            path.stat().st_uid,
            path.stat().st_gid,
            path.stat().st_mtime_ns,
            path.stat().st_ino,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sentinels
    }
    assert after == before, "Synthetic caller SDK content and metadata must remain unchanged"


def test_autouse_fixture_removes_synthetic_flutter_path_from_process(monkeypatch, tmp_path, tmp_path_factory):
    caller_prefix = tmp_path / "caller-prefix"
    caller_flutter_bin = caller_prefix / "opt" / "flutter" / "bin"
    caller_flutter_bin.mkdir(parents=True)
    fake_flutter = caller_flutter_bin / "flutter"
    fake_flutter.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    fake_flutter.chmod(0o755)

    monkeypatch.setenv("PREFIX", str(caller_prefix))
    monkeypatch.setenv("FLUTTER_ROOT", str(caller_prefix / "opt" / "flutter"))
    monkeypatch.setenv("PATH", os.pathsep.join((str(caller_flutter_bin), os.environ.get("PATH", ""))))

    # Invoke the registered autouse fixture body against this pytest process,
    # not just the helper that constructs a proposed child environment.
    isolate_test_user_state.__wrapped__(tmp_path_factory, monkeypatch)

    assert str(caller_flutter_bin) not in os.environ["PATH"].split(os.pathsep)
    assert os.environ["PREFIX"] != str(caller_prefix)
    assert os.environ["FLUTTER_ROOT"] != str(caller_prefix / "opt" / "flutter")
    assert shutil.which("flutter") != str(fake_flutter)


def test_autouse_fixture_guards_native_prefix_flutter_and_dart_wrappers(monkeypatch, tmp_path, tmp_path_factory):
    caller_prefix = tmp_path / "caller-prefix"
    caller_bin = caller_prefix / "bin"
    caller_bin.mkdir(parents=True)
    marker = tmp_path / "caller-wrapper-executed"
    for name in ("flutter", "dart"):
        wrapper = caller_bin / name
        wrapper.write_text(f"#!/bin/sh\nprintf '%s\\n' {name} >> '{marker}'\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)

    monkeypatch.setenv("PREFIX", str(caller_prefix))
    monkeypatch.setenv("FLUTTER_ROOT", str(caller_prefix / "opt" / "flutter"))
    monkeypatch.setenv("PATH", os.pathsep.join((str(caller_bin), os.environ.get("PATH", ""))))

    isolate_test_user_state.__wrapped__(tmp_path_factory, monkeypatch)

    guard_bin = Path(os.environ["TEST_USER_GUARD_BIN"])
    assert shutil.which("flutter") == str(guard_bin / "flutter")
    assert shutil.which("dart") == str(guard_bin / "dart")
    for command in ("flutter", "dart"):
        result = subprocess.run([command, "--version"], capture_output=True, text=True)
        assert result.returncode != 0
        assert "blocked by test SDK isolation" in result.stderr
    assert not marker.exists(), "caller SDK wrappers must never execute during isolation"

    unknown_bin = tmp_path / "caller-unknown-path"
    unknown_bin.mkdir()
    for name in ("flutter", "dart"):
        wrapper = unknown_bin / name
        wrapper.write_text(f"#!/bin/sh\nprintf '%s\\n' {name} >> '{marker}'\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join((str(unknown_bin), os.environ["PATH"])))
    isolate_test_user_state.__wrapped__(tmp_path_factory, monkeypatch)
    refreshed_guard_bin = Path(os.environ["TEST_USER_GUARD_BIN"])
    assert shutil.which("flutter") == str(refreshed_guard_bin / "flutter")
    assert shutil.which("dart") == str(refreshed_guard_bin / "dart")
    assert not marker.exists(), "unknown inherited PATH entries must not precede fresh guards"

    fixture_bin = tmp_path / "fixture-bin"
    fixture_bin.mkdir()
    for name in ("flutter", "dart"):
        mock = fixture_bin / name
        mock.write_text(f"#!/bin/sh\necho 'fixture {name}'\n", encoding="utf-8")
        mock.chmod(0o755)
    child_env = isolated_user_env(tmp_path / "fixture-child-state", fixture_path_entries=(fixture_bin,))
    for name in ("flutter", "dart"):
        assert shutil.which(name, path=child_env["PATH"]) == str(fixture_bin / name)
        result = subprocess.run([name], env=child_env, capture_output=True, text=True)
        assert result.returncode == 0
        assert result.stdout.strip() == f"fixture {name}"
