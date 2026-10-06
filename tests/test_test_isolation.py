"""Regression tests for keeping installer fixtures out of persistent user state."""

import os
from pathlib import Path

from conftest import _installed_dartvm, isolated_user_env
from test_offline_cache_repair import create_mock_env, run_post_install


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
    result = run_post_install(flutter_root, android_sdk, prefix, ["--apply"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert settings.read_text() == original_settings
    assert unrelated.read_text() == "preserve caller data\n"
    assert str(android_sdk) not in settings.read_text()
    fixture_settings = prefix.parent / ".post-install-user-state" / "home" / ".flutter_settings"
    assert str(android_sdk) in fixture_settings.read_text()


def test_pytest_process_uses_test_local_user_directories(tmp_path):
    assert Path(os.environ["HOME"]).is_relative_to(tmp_path)
    for variable in (
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "GRADLE_USER_HOME",
        "PUB_CACHE",
        "ANDROID_USER_HOME",
        "ANDROID_SDK_HOME",
    ):
        assert Path(os.environ[variable]).is_relative_to(tmp_path)
    assert Path(os.environ["TMPDIR"]).is_relative_to(tmp_path)
    dartvm = os.environ.get("DARTVM")
    if dartvm:
        assert Path(dartvm).is_absolute()


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
