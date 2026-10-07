import os
import json
import subprocess
from pathlib import Path
import pytest

from conftest import to_bash_path, to_wsl_posix

REPO_ROOT = Path(__file__).parent.parent
SCRIPT_PATH = (REPO_ROOT / "scripts" / "device" / "termux_smoke.sh").relative_to(REPO_ROOT).as_posix()

def test_termux_smoke_script_syntax():
    res = subprocess.run(["bash", "-n", SCRIPT_PATH], cwd=REPO_ROOT, capture_output=True, text=True)
    assert res.returncode == 0, f"Syntax error in termux_smoke.sh: {res.stderr}"

def test_termux_smoke_evidence_structure(tmp_path):
    source = (REPO_ROOT / SCRIPT_PATH).read_text(encoding="utf-8")
    functions_start = source.index("record_status() {")
    functions_end = source.index("\ntrap write_evidence_json EXIT", functions_start)
    evidence_functions = source[functions_start:functions_end]

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    marker_dir = tmp_path / "blocked-commands"
    marker_dir.mkdir()
    blocked_calls = tmp_path / "blocked-calls.log"
    blocked_commands = ("su", "apt-get", "dpkg", "flutter", "dart", "adb", "am", "chmod")
    for command in blocked_commands:
        wrapper = marker_dir / command
        wrapper.write_text(f"#!/bin/sh\necho {command} >> '{blocked_calls}'\nexit 99\n", encoding="utf-8")
        wrapper.chmod(0o755)

    # Evaluate only the evidence-formatting functions. Never execute the device
    # smoke script's install, post-install, doctor, network, or build workflow.
    harness = "\n".join((
        "set -eu",
        "getprop() { echo test-device; }",
        "cp() { test \"$2\" = /sdcard/Download/evidence.json; }",
        evidence_functions,
        "record_status APK_HOST_VERIFY_REQUIRED 0",
        "write_evidence_json",
    ))
    env = os.environ.copy()
    env.update({
        "HOME": str(home_dir),
        "PATH": f"{marker_dir}{os.pathsep}{env.get('PATH', '')}",
        "TERMUX_SMOKE_COMMIT_SHA": "abc123def",
    })
    res = subprocess.run(["bash", "-c", harness], cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    ev_path = home_dir / ".termux_smoke" / "evidence.json"
    assert ev_path.exists(), f"evidence.json not created.\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    assert res.returncode == 0, f"Evidence formatter failed: {res.stderr}"
    assert not blocked_calls.exists(), f"Device workflow command unexpectedly ran: {blocked_calls.read_text()}"

    data = json.loads(ev_path.read_text())
    assert "status" in data
    assert "apk_launch" in data
    assert "crash_free" in data
    assert "commit_sha" in data
    assert data["commit_sha"] == "abc123def"
    assert "mode_a" in data
    assert "mode_b" in data


def test_termux_smoke_delegated_launch_emits_pending_host_marker(tmp_path):
    log_file = tmp_path / "smoke.log"
    script = (
        f"export TERMUX_SMOKE_LOG='{to_wsl_posix(log_file)}'\n"
        "record_status() { echo \"$1=$2\" >> \"$TERMUX_SMOKE_LOG\"; }\n"
        "ALLOW_LOCAL_TERMUX_LAUNCH=0\n"
        "if [ \"${ALLOW_LOCAL_TERMUX_LAUNCH:-0}\" = \"1\" ]; then\n"
        "  record_status APK_LAUNCH_STATUS 0\n"
        "else\n"
        "  record_status APK_HOST_VERIFY_REQUIRED 0\n"
        "fi\n"
    )
    sh_file = tmp_path / "test.sh"
    sh_file.write_text(script, encoding="utf-8", newline="\n")
    res = subprocess.run(["bash", to_wsl_posix(sh_file)], cwd=REPO_ROOT, capture_output=True, text=True)
    assert res.returncode == 0, f"Command failed: {res.stderr}"
    log_content = log_file.read_text()
    assert "APK_HOST_VERIFY_REQUIRED=0" in log_content
    assert "APK_LAUNCH_STATUS=0" not in log_content


def test_mode_b_failure_fails_overall_status():
    ps_script = (REPO_ROOT / "scripts" / "device" / "run_termux_smoke.ps1").read_text(encoding="utf-8")
    assert 'BUILD_AAB_STATUS=0' in ps_script
    assert 'AAB_COPY_STATUS=0' in ps_script
    assert 'Invoke-Adb -Args @("pull", "/sdcard/Download/app-release.aab", $localAab)' in ps_script
    assert 'overallStatus = if ($launchPassed -and $modeA -eq "passed" -and $modeB -eq "passed") { "passed" } else { "failed" }' in ps_script


def test_host_adb_pid_liveness_and_crash_parsing_logic():
    """Verify PID liveness state machine and scoped crash detection rules in run_termux_smoke.ps1."""
    ps1_text = (REPO_ROOT / "scripts" / "device" / "run_termux_smoke.ps1").read_text(encoding="utf-8")

    assert "$initialPid" in ps1_text
    assert "$pidCurrent -ne $initialPid" in ps1_text
    assert "$livenessPassed = $false" in ps1_text
    assert "com.example.flutter_ci_smoke" in ps1_text
    assert "initial_pid" in ps1_text
    assert "app_pid" in ps1_text
    assert "artifact_source_commit" in ps1_text
    assert "verifier_commit" in ps1_text


def test_candidate_install_failure_with_old_package_fails(tmp_path):
    """Regression test: when candidate install fails but old flutter package version is present, INSTALL_STATUS must be 1."""
    log_file = tmp_path / "smoke.log"
    script = (
        f"export TERMUX_SMOKE_LOG='{to_wsl_posix(log_file)}'\n"
        "record_status() { echo \"$1=$2\" >> \"$TERMUX_SMOKE_LOG\"; }\n"
        "EXPECTED_PACKAGE='flutter'\n"
        "EXPECTED_VERSION='3.44.0'\n"
        "EXPECTED_ARCH='aarch64'\n"
        "status_APT_REPAIR_STATUS=0\n"
        "# Old package installed: version 3.3.0 instead of candidate 3.44.0\n"
        "PKG_STATUS='install ok installed'\n"
        "ACTUAL_PKG='flutter'\n"
        "ACTUAL_VER='3.3.0'\n"
        "ACTUAL_ARCH='aarch64'\n"
        "if [ \"${status_APT_REPAIR_STATUS:-1}\" = \"0\" ] && \\\n"
        "   [ \"$PKG_STATUS\" = \"install ok installed\" ] && \\\n"
        "   [ \"$ACTUAL_PKG\" = \"$EXPECTED_PACKAGE\" ] && \\\n"
        "   [ \"$ACTUAL_VER\" = \"$EXPECTED_VERSION\" ] && \\\n"
        "   [ \"$ACTUAL_ARCH\" = \"$EXPECTED_ARCH\" ]; then\n"
        "    record_status INSTALL_STATUS 0\n"
        "else\n"
        "    record_status INSTALL_STATUS 1\n"
        "fi\n"
    )
    sh_file = tmp_path / "test_old_pkg.sh"
    sh_file.write_text(script, encoding="utf-8", newline="\n")
    res = subprocess.run(["bash", to_wsl_posix(sh_file)], cwd=REPO_ROOT, capture_output=True, text=True)
    assert res.returncode == 0
    log_content = log_file.read_text()
    assert "INSTALL_STATUS=1" in log_content
    assert "INSTALL_STATUS=0" not in log_content
