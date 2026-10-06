import hashlib

import os

from pathlib import Path

import shutil

import subprocess

import pytest

from test_offline_cache_repair import create_mock_env, run_post_install

REPO_ROOT = Path(__file__).resolve().parents[1]

POST_INSTALL = REPO_ROOT / "scripts/install/post_install.sh"

ENGINE_PATCH = REPO_ROOT / "patches/3.47.2/flutter_sdk_arm64_default.patch"

def _dartvm_path():
    return os.environ.get("DARTVM") or shutil.which("dartvm")

def test_android_snapshot_cache_skips_linux_x64_bundles_on_arm64_host():
    source = POST_INSTALL.read_text()
    patch = ENGINE_PATCH.read_text()
    assert "termuxLinuxArm64Host" in source
    assert "cache.getHostPlatformArchName()" in source
    assert "cache.getHostPlatformArchName() == 'linux-arm64'" in patch
    assert "if (!termuxLinuxArm64Host) ..._linuxBinaryDirs" in patch
    assert "sdk.first != 'linux-x64'" in patch
    assert "...termuxDartSdks" in patch
    assert "_platform.isLinux && !termuxLinuxArm64Host" in patch

@pytest.mark.parametrize(
    ("indent", "font_preimage", "expected_font_line"),
    [
        (
            "      ",
            "final List<String>? binaryDirs = artifacts[_platform.operatingSystem];",
            'final List<String>? binaryDirs = artifacts[_platform.isAndroid ? "linux" : _platform.operatingSystem]; // Termux: map Android host to Linux artifacts.',
        ),
        (
            "      ",
            'final List<String>? binaryDirs = artifacts[_platform.isAndroid ? "linux" : _platform.operatingSystem]; // Termux: map Android host to Linux artifacts.',
            'final List<String>? binaryDirs = artifacts[_platform.isAndroid ? "linux" : _platform.operatingSystem]; // Termux: map Android host to Linux artifacts.',
        ),
        (
            "      ",
            "final List<String>? binaryDirs = artifacts[_platform.isAndroid ? 'linux' : _platform.operatingSystem]; // Termux: map Android host to Linux artifacts",
            "final List<String>? binaryDirs = artifacts[_platform.isAndroid ? 'linux' : _platform.operatingSystem]; // Termux: map Android host to Linux artifacts",
        ),
    ],
)
def test_post_install_patches_old_font_cache_and_new_snapshot_selection(
    tmp_path, indent, font_preimage, expected_font_line
):
    flutter_root, android_sdk, prefix, _ = create_mock_env(tmp_path)
    cache_file = flutter_root / "packages/flutter_tools/lib/src/flutter_cache.dart"
    original = cache_file.read_text()
    _, _, remainder = original.partition("\n")
    cache_file.write_text(indent + font_preimage + "\n" + remainder)
    old_hash = hashlib.sha256(cache_file.read_bytes()).hexdigest()
    state_file = prefix / "share/flutter/patch_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        "{\n"
        '  "flutter_cache": {\n'
        f'    "target": "{cache_file}",\n'
        f'    "postimage": "{old_hash}",\n'
        '    "func_digest": "previous-installer-function",\n'
        '    "status": "applied"\n'
        "  }\n"
        "}\n"
    )

    result = run_post_install(flutter_root, android_sdk, prefix)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "patch updated, re-applying" in result.stdout
    patched = cache_file.read_text()
    assert indent + expected_font_line in patched
    assert "cache.getHostPlatformArchName() == 'linux-arm64'" in patched
    assert "if (!termuxLinuxArm64Host) ..._linuxBinaryDirs" in patched
    assert "_platform.isLinux && !termuxLinuxArm64Host" in patched
    assert "sdk.first != 'linux-x64'" in patched
    assert "...termuxDartSdks" in patched

    result = run_post_install(flutter_root, android_sdk, prefix)
    assert result.returncode == 0, result.stdout + result.stderr
    assert cache_file.read_text() == patched

def test_dartvm_executes_extracted_patched_binary_dir_method(tmp_path):
    dartvm = _dartvm_path()
    if not dartvm:
        pytest.skip("set DARTVM or put dartvm on PATH to execute the extracted Dart method")

    flutter_root, android_sdk, prefix, _ = create_mock_env(tmp_path)
    result = run_post_install(flutter_root, android_sdk, prefix)
    assert result.returncode == 0, result.stdout + result.stderr
    cache_source = (flutter_root / "packages/flutter_tools/lib/src/flutter_cache.dart").read_text()
    start = cache_source.index("  List<List<String>> getBinaryDirs() {")
    cursor = cache_source.index("{", start)
    depth = 0
    for end in range(cursor, len(cache_source)):
        depth += (cache_source[end] == "{") - (cache_source[end] == "}")
        if depth == 0:
            end += 1
            break
    method = cache_source[start:end]

    harness = tmp_path / "snapshot_cache_harness.dart"
    harness.write_text(
        """const _osxBinaryDirs = <List<String>>[['darwin-host']];
const _linuxBinaryDirs = <List<String>>[['linux-host']];
const _windowsBinaryDirs = <List<String>>[['windows-host']];
const _dartSdks = <List<String>>[
  ['darwin-x64', 'dart-sdk-darwin-x64.zip'],
  ['linux-x64', 'dart-sdk-linux-x64.zip'],
  ['windows-x64', 'dart-sdk-windows-x64.zip'],
];
class Cache {
  Cache(this.includeAllPlatforms, this.host);
  final bool includeAllPlatforms;
  final String host;
  String getHostPlatformArchName() => host;
}
class Platform {
  Platform({this.isWindows = false, this.isMacOS = false, this.isLinux = false});
  final bool isWindows, isMacOS, isLinux;
}
class AndroidGenSnapshotArtifacts {
  AndroidGenSnapshotArtifacts(this.cache, this._platform);
  final Cache cache;
  final Platform _platform;
"""
        + method
        + """
}
void check(bool value, String message) { if (!value) throw StateError(message); }
void main() {
  final armAll = AndroidGenSnapshotArtifacts(Cache(true, 'linux-arm64'), Platform(isLinux: true)).getBinaryDirs();
  check(!armAll.any((entry) => entry.first == 'linux-host'), 'ARM64 includeAll selected Linux host binaries');
  check(!armAll.any((entry) => entry.first == 'linux-x64'), 'ARM64 includeAll selected x64 Dart SDK');
  check(armAll.any((entry) => entry.first == 'darwin-host'), 'includeAll lost Darwin bundles');
  check(armAll.any((entry) => entry.first == 'windows-host'), 'includeAll lost Windows bundles');
  final armLinux = AndroidGenSnapshotArtifacts(Cache(false, 'linux-arm64'), Platform(isLinux: true)).getBinaryDirs();
  check(armLinux.isEmpty, 'ARM64 direct Linux selected incompatible bundles');
  final x64Linux = AndroidGenSnapshotArtifacts(Cache(false, 'linux-x64'), Platform(isLinux: true)).getBinaryDirs();
  check(x64Linux.any((entry) => entry.first == 'linux-host'), 'Linux x64 behavior changed');
  final windows = AndroidGenSnapshotArtifacts(Cache(false, 'windows-x64'), Platform(isWindows: true)).getBinaryDirs();
  check(windows.any((entry) => entry.first == 'windows-host'), 'Windows behavior changed');
  print('patched Android snapshot selection: PASS');
}
"""
    )
    outcome = subprocess.run([dartvm, str(harness)], capture_output=True, text=True)
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr
    assert "patched Android snapshot selection: PASS" in outcome.stdout
