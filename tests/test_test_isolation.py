"""Regression tests for keeping installer fixtures out of persistent user state."""

import os
from pathlib import Path
import sys

from conftest import _installed_dartvm, isolated_user_env
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
