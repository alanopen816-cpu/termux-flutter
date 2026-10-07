import json
import os
import shlex
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from scripts.install import flutter_package_bootstrap as bootstrap


def _android_elf(path: Path):
    ident = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    header = struct.pack("<16sHHIQQQIHHHHHH", ident, 3, 183, 1, 0, 64, 0, 0, 64, 56, 1, 64, 0, 0)
    interp = b"/system/bin/linker64\0"
    phdr = struct.pack("<IIQQQQQQ", 3, 4, 120, 0, 0, len(interp), len(interp), 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + phdr + b"\0" * (120 - len(header) - len(phdr)) + interp)
    path.chmod(0o755)


def _package_fixture(tmp_path: Path, *, complete_sdk=True):
    prefix = tmp_path / "prefix"
    root = prefix / "opt/flutter"
    sdk = prefix / "opt/android-sdk"
    home = tmp_path / "home"
    home.mkdir()
    tools = root / "packages/flutter_tools"
    cache = root / "bin/cache"
    dart_bin = cache / "dart-sdk/bin"
    for name in ("dart", "dartvm", "dartaotruntime"):
        _android_elf(dart_bin / name)
    devtools_version = dart_bin / "resources/devtools/version.json"
    devtools_version.parent.mkdir(parents=True)
    devtools_version.write_text(json.dumps({"app_name": "devtools_app", "version": "2.60.0",
                                            "package_name": "devtools_app"}))
    for mode in ("release", "profile"):
        _android_elf(cache / f"artifacts/engine/android-arm64-{mode}/linux-arm64/gen_snapshot")
    (tools / "bin").mkdir(parents=True)
    (tools / "bin/flutter_tools.dart").write_text("void main() {}\n")
    (tools / "pubspec.yaml").write_text("name: flutter_tools\n")
    (tools / "pubspec.lock").write_text("# resolved\n")
    (tools / ".dart_tool").mkdir()
    (tools / ".dart_tool/package_config.json").write_text(json.dumps({"configVersion": 2, "packages": []}))
    (root / "bin/internal").mkdir(parents=True)
    (root / "bin/internal/shared.sh").write_text("# shared\n")
    engine = "e" * 40
    (root / "bin/internal/engine.version").write_text(engine + "\n")
    (root / "bin/version").write_text("3.47.2\n")
    (root / "version").write_text("3.47.2\n")
    (cache / "flutter_tools.snapshot").write_bytes(b"snapshot bytes\n")
    (cache / "engine.stamp").write_text(engine + "\n")
    (cache / "engine_stamp.stamp").write_text(engine + "\n")
    (cache / "engine_stamp.json").write_text(json.dumps({
        "build_time_ms": 1, "git_revision": engine, "git_revision_date": "2026-01-01T00:00:00Z",
        "content_hash": engine,
    }))
    git = root / ".git"
    git.mkdir()
    (git / "termux_synthetic").write_text("synthetic-v1\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "packages/flutter_tools", "bin", "version"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    (cache / "flutter_tools.stamp").write_text(f"{revision}:\n")
    if complete_sdk:
        (sdk / "platforms/android-34").mkdir(parents=True)
        (sdk / "platforms/android-34/android.jar").write_bytes(b"platform")
        (sdk / "licenses").mkdir(parents=True)
        (sdk / "platform-tools").mkdir(parents=True)
        (sdk / "platform-tools/adb").write_bytes(b"adb")
        (sdk / "build-tools/35.0.0").mkdir(parents=True)
    return prefix, root, sdk, home


def _mock_native_loader(monkeypatch):
    real_run = subprocess.run
    invocations = []

    def run(command, *args, **kwargs):
        if Path(command[0]).name == "dart" and "--packages=" in " ".join(command):
            invocations.append((command, kwargs))
            assert "--no-version-check" in command and "--suppress-analytics" in command
            snapshot = Path(command[-4])
            assert snapshot.is_file() and snapshot.stat().st_size > 0
            flutter_root = Path(kwargs["env"]["FLUTTER_ROOT"])
            devtools = json.loads((flutter_root / "bin/cache/dart-sdk/bin/resources/devtools/version.json").read_text())
            assert devtools["version"] == "2.60.0"
            return subprocess.CompletedProcess(command, 0, stdout="Flutter fixture\n", stderr="")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    return invocations


def test_bootstrap_validates_isolated_snapshot_before_android_configuration(tmp_path, monkeypatch):
    prefix, root, sdk, home = _package_fixture(tmp_path)
    loads = _mock_native_loader(monkeypatch)
    calls = []
    caller_tmp = tmp_path / "caller-tmp"
    caller_tmp.mkdir()
    monkeypatch.setenv("TMPDIR", str(caller_tmp))
    monkeypatch.setattr(bootstrap.tempfile, "tempdir", None)
    monkeypatch.setattr(bootstrap._android_config, "is_termux_runtime", lambda _prefix: True)
    monkeypatch.setattr(bootstrap, "configure_android", lambda *args: calls.append(args) or True)
    bootstrap.configure_package(prefix, home, root, sdk, tool_args="")
    assert calls == [(prefix, home, sdk)]
    assert len(loads) == 1
    loaded_snapshot = Path(loads[0][0][-4])
    assert root not in loaded_snapshot.parents
    assert "flutter-package-check-" in str(loaded_snapshot)
    assert loads[0][1]["env"]["HOME"].startswith(str(caller_tmp / "flutter-package-check-"))


def test_bootstrap_repeat_preserves_configured_files(tmp_path, monkeypatch):
    prefix, root, sdk, home = _package_fixture(tmp_path)
    _mock_native_loader(monkeypatch)
    (prefix / "bin").mkdir(parents=True)
    _android_elf(prefix / "bin/aapt2")
    monkeypatch.setattr(bootstrap._android_config, "is_termux_runtime", lambda _prefix: True)
    bootstrap.configure_package(prefix, home, root, sdk, tool_args="")
    targets = [home / ".flutter_settings", Path(os.environ["GRADLE_USER_HOME"]) / "gradle.properties"]
    before = [(target.read_bytes(), target.stat().st_mode, target.stat().st_mtime_ns, target.stat().st_ino)
              for target in targets]
    bootstrap.configure_package(prefix, home, root, sdk, tool_args="")
    after = [(target.read_bytes(), target.stat().st_mode, target.stat().st_mtime_ns, target.stat().st_ino)
             for target in targets]
    assert after == before


def test_bootstrap_missing_sdk_fails_before_any_persistent_configuration(tmp_path, monkeypatch):
    prefix, root, sdk, home = _package_fixture(tmp_path, complete_sdk=False)
    calls = []
    monkeypatch.setattr(bootstrap._android_config, "is_termux_runtime", lambda _prefix: True)
    _mock_native_loader(monkeypatch)
    monkeypatch.setattr(bootstrap, "configure_android", lambda *_args, **_kwargs: calls.append(True))
    with pytest.raises(bootstrap.BootstrapError, match="Android SDK is incomplete"):
        bootstrap.configure_package(prefix, home, root, sdk, tool_args="")
    assert not calls


@pytest.mark.parametrize("damage", ["stamp", "engine-json", "source", "snapshot"])
def test_bootstrap_rejects_corrupt_package_before_configuration(tmp_path, monkeypatch, damage):
    prefix, root, sdk, home = _package_fixture(tmp_path)
    cache = root / "bin/cache"
    if damage == "stamp":
        (cache / "flutter_tools.stamp").write_text("wrong-head:\n")
    elif damage == "engine-json":
        (cache / "engine_stamp.json").write_text("{}")
    elif damage == "source":
        (root / "packages/flutter_tools/bin/flutter_tools.dart").unlink()
    else:
        (cache / "flutter_tools.snapshot").write_bytes(b"")
    calls = []
    monkeypatch.setattr(bootstrap._android_config, "is_termux_runtime", lambda _prefix: True)
    _mock_native_loader(monkeypatch)
    monkeypatch.setattr(bootstrap, "configure_android", lambda *_args, **_kwargs: calls.append(True))
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.configure_package(prefix, home, root, sdk, tool_args="")
    assert not calls


@pytest.mark.parametrize("damage", ["missing", "invalid"])
def test_bootstrap_requires_devtools_version_before_any_changes(tmp_path, monkeypatch, damage):
    prefix, root, sdk, home = _package_fixture(tmp_path)
    version_path = root / "bin/cache/dart-sdk/bin/resources/devtools/version.json"
    if damage == "missing":
        version_path.unlink()
    else:
        version_path.write_text('{"version": 4}')
    snapshot = root / "bin/cache/flutter_tools.snapshot"
    stamp = root / "bin/cache/flutter_tools.stamp"
    original_pair = snapshot.read_bytes(), stamp.read_bytes()
    settings = home / ".flutter_settings"
    settings.write_text('{"keep":true}\n')
    original_settings = settings.read_bytes()
    monkeypatch.setattr(bootstrap._android_config, "is_termux_runtime", lambda _prefix: True)
    calls = []
    monkeypatch.setattr(bootstrap, "configure_android", lambda *_args: calls.append(True))
    with pytest.raises(bootstrap.BootstrapError, match="DevTools version metadata"):
        bootstrap.configure_package(prefix, home, root, sdk, tool_args="")
    assert not calls
    assert (snapshot.read_bytes(), stamp.read_bytes()) == original_pair
    assert settings.read_bytes() == original_settings


def test_bounded_snapshot_diagnostic_preserves_header_and_tail():
    diagnostic = bootstrap._bounded_diagnostic("ROOT-EXCEPTION:" + ("x" * 3000) + ":TAIL")
    assert len(diagnostic) <= 1200
    assert diagnostic.startswith("ROOT-EXCEPTION:")
    assert diagnostic.endswith(":TAIL")


def test_bootstrap_rejects_external_package_uri(tmp_path, monkeypatch):
    prefix, root, sdk, home = _package_fixture(tmp_path)
    config = root / "packages/flutter_tools/.dart_tool/package_config.json"
    config.write_text(json.dumps({"configVersion": 2, "packages": [{"name": "external", "rootUri": "https://example.invalid/pkg"}]}))
    monkeypatch.setattr(bootstrap._android_config, "is_termux_runtime", lambda _prefix: True)
    _mock_native_loader(monkeypatch)
    with pytest.raises(bootstrap.BootstrapError, match="must be relative to the SDK"):
        bootstrap.configure_package(prefix, home, root, sdk, tool_args="")


def test_bootstrap_rejects_percent_encoded_root_escape(tmp_path, monkeypatch):
    prefix, root, sdk, home = _package_fixture(tmp_path)
    config = root / "packages/flutter_tools/.dart_tool/package_config.json"
    config.write_text(json.dumps({"configVersion": 2, "packages": [{
        "name": "escape", "rootUri": "%2e%2e/%2e%2e/%2e%2e/%2e%2e/outside/",
    }]}))
    monkeypatch.setattr(bootstrap._android_config, "is_termux_runtime", lambda _prefix: True)
    with pytest.raises(bootstrap.BootstrapError, match="escapes or is missing"):
        bootstrap.configure_package(prefix, home, root, sdk, tool_args="")


def test_bootstrap_fails_closed_outside_termux_before_validation_or_writes(tmp_path, monkeypatch):
    prefix, root, sdk, home = _package_fixture(tmp_path)
    monkeypatch.setattr(bootstrap._android_config, "is_termux_runtime", lambda _prefix: False)
    monkeypatch.setattr(bootstrap, "validate_package", lambda *_args: pytest.fail("must not validate"))
    with pytest.raises(bootstrap.BootstrapError, match="only in the native Termux runtime"):
        bootstrap.configure_package(prefix, home, root, sdk, tool_args="")


def test_postinst_configure_dispatches_checked_bootstrap_once(tmp_path):
    result, record = _run_postinst(tmp_path, "configure")
    assert result.returncode == 0, result.stdout + result.stderr
    assert record.read_text().splitlines() == ["--package-bootstrap"]


def _run_postinst(tmp_path, argument, *, result_code=0, preload=None, bash=True):
    import os
    import subprocess

    from pathlib import Path
    postinst = Path(__file__).parents[1] / "scripts/install/postinst.sh"
    prefix = tmp_path / "prefix"
    installed = prefix / "share/flutter"
    installed.mkdir(parents=True)
    record = tmp_path / "called.txt"
    bash_seen = tmp_path / "bash-environment.txt"
    if bash:
        (prefix / "bin").mkdir(parents=True, exist_ok=True)
        interpreter = prefix / "bin/bash"
        host_bash = shutil.which("bash")
        assert host_bash
        interpreter.write_text(
            f"#!/bin/sh\nprintf '%s' \"${{LD_PRELOAD-<unset>}}\" > {shlex.quote(str(bash_seen))}\n"
            f"exec {shlex.quote(host_bash)} \"$@\"\n"
        )
        interpreter.chmod(0o755)
    stub = installed / "post_install.sh"
    stub.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {record}\nexit {result_code}\n")
    stub.chmod(0o755)
    env = os.environ.copy()
    env.update({"PREFIX": str(prefix), "HOME": str(tmp_path / "home")})
    if preload is not None:
        env["LD_PRELOAD"] = preload
    else:
        env.pop("LD_PRELOAD", None)
    return subprocess.run(["bash", str(postinst), argument], env=env, text=True, capture_output=True), record


@pytest.mark.parametrize("argument", ["abort-upgrade", "abort-remove", "abort-deconfigure"])
def test_postinst_abort_actions_are_noops(tmp_path, argument):
    result, record = _run_postinst(tmp_path, argument)
    assert result.returncode == 0
    assert not record.exists()


def test_postinst_triggered_is_a_noop(tmp_path):
    result, record = _run_postinst(tmp_path, "triggered")
    assert result.returncode == 0
    assert not record.exists()


def test_postinst_executes_through_prefix_bash_without_inherited_ld_preload(tmp_path):
    # Empty-but-present avoids the Android loader refusing to start the test shell;
    # the prefix Bash wrapper verifies the variable is truly unset for the child.
    result, record = _run_postinst(tmp_path, "configure", preload="")
    assert result.returncode == 0, result.stderr
    assert record.read_text().splitlines() == ["--package-bootstrap"]
    assert (tmp_path / "bash-environment.txt").read_text() == "<unset>"


def test_postinst_requires_native_prefix_bash(tmp_path):
    result, record = _run_postinst(tmp_path, "configure", bash=False)
    assert result.returncode != 0
    assert "required Termux Bash is missing" in result.stderr
    assert not record.exists()


def test_postinst_propagates_critical_bootstrap_failure(tmp_path):
    result, record = _run_postinst(tmp_path, "configure", result_code=17)
    assert result.returncode == 17
    assert record.read_text().splitlines() == ["--package-bootstrap"]
