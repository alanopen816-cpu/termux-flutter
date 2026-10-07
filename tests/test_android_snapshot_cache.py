import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import zipfile
import pytest

from test_offline_cache_repair import create_mock_env, run_post_install


REPO_ROOT = Path(__file__).resolve().parents[1]
POST_INSTALL = REPO_ROOT / "scripts/install/post_install.sh"


def _synthetic_elf(machine, interpreter, marker):
    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4:6] = b"\x02\x01"
    struct.pack_into("<H", header, 16, 2)
    struct.pack_into("<H", header, 18, machine)
    struct.pack_into("<Q", header, 32, 64)
    struct.pack_into("<H", header, 52, 64)
    struct.pack_into("<H", header, 54, 56)
    struct.pack_into("<H", header, 56, 1)
    interp = interpreter.encode() + b"\0"
    program = bytearray(56)
    struct.pack_into("<I", program, 0, 3)
    struct.pack_into("<Q", program, 8, 120)
    struct.pack_into("<Q", program, 32, len(interp))
    struct.pack_into("<Q", program, 40, len(interp))
    return bytes(header + program) + interp + marker


def _elf_identity(path):
    data = path.read_bytes()
    assert data[:6] == b"\x7fELF\x02\x01"
    assert struct.unpack_from("<H", data, 16)[0] == 2
    machine = struct.unpack_from("<H", data, 18)[0]
    offset = struct.unpack_from("<Q", data, 32)[0]
    entry_size = struct.unpack_from("<H", data, 54)[0]
    count = struct.unpack_from("<H", data, 56)[0]
    interpreter = None
    for index in range(count):
        program = data[offset + index * entry_size : offset + (index + 1) * entry_size]
        if struct.unpack_from("<I", program)[0] == 3:
            file_offset = struct.unpack_from("<Q", program, 8)[0]
            size = struct.unpack_from("<Q", program, 32)[0]
            interpreter = data[file_offset : file_offset + size].rstrip(b"\0").decode()
    return machine, interpreter


def _dartvm_path():
    return os.environ.get("DARTVM") or shutil.which("dartvm")


def _run_flutter_cache_patch(target, flutter_root, android_sdk, prefix):
    env = os.environ.copy()
    env.update(
        {
            "FLUTTER_ROOT": str(flutter_root),
            "ANDROID_SDK": str(android_sdk),
            "PREFIX": str(prefix),
            "DART_SDK": str(flutter_root / "bin/cache/dart-sdk"),
            "TMPDIR": str(prefix / "tmp"),
            "CACHE_PATCH_TARGET": str(target),
            "POST_INSTALL_SCRIPT": str(POST_INSTALL),
        }
    )
    command = 'source "$POST_INSTALL_SCRIPT" --lib && patch_flutter_cache "$CACHE_PATCH_TARGET"'
    return subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True)


def test_android_snapshot_cache_skips_linux_x64_bundles_on_arm64_host():
    source = POST_INSTALL.read_text()
    assert "termuxLinuxArm64Host" in source
    assert "cache.getHostPlatformArchName()" in source
    assert "_platform.environment.containsKey('TERMUX_VERSION')" in source
    assert "cache.getHostPlatformArchName() == 'arm64'" in source
    assert "if (!termuxLinuxArm64Host) ..._linuxBinaryDirs" in source
    assert "sdk.first != 'linux-x64'" in source
    assert "...termuxDartSdks" in source
    assert "_platform.isLinux && !termuxLinuxArm64Host" in source


def test_post_install_does_not_alias_linux_x64_to_packaged_arm64():
    source = POST_INSTALL.read_text()
    assert 'ln -sf linux-arm64 "$ENG_ART/$dir/linux-x64"' not in source
    assert 'ln -sf linux-arm64 "$ENG_ART/linux-x64"' not in source
    assert "remove_stale_android_snapshot_aliases" in source


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
    assert "_platform.environment.containsKey('TERMUX_VERSION')" in patched
    assert "cache.getHostPlatformArchName() == 'arm64'" in patched
    assert "if (!termuxLinuxArm64Host) ..._linuxBinaryDirs" in patched
    assert "_platform.isLinux && !termuxLinuxArm64Host" in patched
    assert "sdk.first != 'linux-x64'" in patched
    assert "...termuxDartSdks" in patched

    result = run_post_install(flutter_root, android_sdk, prefix)
    assert result.returncode == 0, result.stdout + result.stderr
    assert cache_file.read_text() == patched


@pytest.mark.parametrize(
    "variant",
    ["partial_marker", "method_indent", "dart_sdk_spread_indent", "missing_snapshot_class"],
)
def test_incomplete_flutter_cache_migration_fails_without_mutating_source(tmp_path, variant):
    flutter_root, android_sdk, prefix, _ = create_mock_env(tmp_path)
    cache_file = flutter_root / "packages/flutter_tools/lib/src/flutter_cache.dart"
    original = cache_file.read_text()
    font_preimage = "final List<String>? binaryDirs = artifacts[_platform.operatingSystem];"

    if variant == "partial_marker":
        original = original.replace(font_preimage, font_preimage + "\nfinal bool termuxLinuxArm64Host = false;", 1)
    elif variant == "method_indent":
        original = original.replace(
            "  List<List<String>> getBinaryDirs() {",
            "    List<List<String>> getBinaryDirs() {",
            1,
        )
    elif variant == "dart_sdk_spread_indent":
        original = original.replace("        ..._dartSdks,", "          ..._dartSdks,", 1)
    elif variant == "missing_snapshot_class":
        original = font_preimage + "\nclass OtherArtifacts {}\n"

    cache_file.write_text(original)
    cache_file.chmod(0o640)
    original_hash = hashlib.sha256(cache_file.read_bytes()).hexdigest()
    original_mode = cache_file.stat().st_mode & 0o777

    result = _run_flutter_cache_patch(cache_file, flutter_root, android_sdk, prefix)

    assert result.returncode != 0, result.stdout + result.stderr
    assert hashlib.sha256(cache_file.read_bytes()).hexdigest() == original_hash
    assert cache_file.stat().st_mode & 0o777 == original_mode


def test_flutter_cache_patch_preserves_mode_after_success(tmp_path):
    flutter_root, android_sdk, prefix, _ = create_mock_env(tmp_path)
    cache_file = flutter_root / "packages/flutter_tools/lib/src/flutter_cache.dart"
    cache_file.chmod(0o640)

    result = _run_flutter_cache_patch(cache_file, flutter_root, android_sdk, prefix)

    assert result.returncode == 0, result.stdout + result.stderr
    assert cache_file.stat().st_mode & 0o777 == 0o640
    patched = cache_file.read_text()
    assert "_platform.environment.containsKey('TERMUX_VERSION')" in patched
    assert "cache.getHostPlatformArchName() == 'arm64'" in patched
    assert "if (!termuxLinuxArm64Host) ..._linuxBinaryDirs" in patched
    assert "sdk.first != 'linux-x64'" in patched
    assert "...termuxDartSdks" in patched


@pytest.mark.parametrize("guard_direct_spread", [False, True])
def test_flutter_cache_patch_accepts_legacy_guarded_linux_selection(tmp_path, guard_direct_spread):
    flutter_root, android_sdk, prefix, _ = create_mock_env(tmp_path)
    cache_file = flutter_root / "packages/flutter_tools/lib/src/flutter_cache.dart"
    source = cache_file.read_text()
    source = source.replace(
        "final List<String>? binaryDirs = artifacts[_platform.operatingSystem];",
        'final List<String>? binaryDirs = artifacts[_platform.isAndroid ? "linux" : _platform.operatingSystem]; // Termux: map Android host to Linux artifacts.',
        1,
    )
    source = source.replace(
        "  List<List<String>> getBinaryDirs() {",
        "  List<List<String>> getBinaryDirs() {\n"
        "    final bool termuxLinuxArm64Host = cache.getHostPlatformArchName() == 'linux-arm64';\n"
        "    final List<List<String>> termuxDartSdks = termuxLinuxArm64Host ? _dartSdks.where((sdk) => sdk.first != 'linux-x64').toList() : _dartSdks;",
        1,
    )
    source = source.replace(
        "        ..._linuxBinaryDirs,",
        "        if (!termuxLinuxArm64Host) ..._linuxBinaryDirs,",
        1,
    )
    source = source.replace("        ..._dartSdks,", "        ...termuxDartSdks,", 1)
    source = source.replace(
        "      else if (_platform.isLinux)",
        "      else if (_platform.isLinux && !termuxLinuxArm64Host)",
        1,
    )
    if guard_direct_spread:
        source = source.replace(
            "      else if (_platform.isLinux && !termuxLinuxArm64Host)\n        ..._linuxBinaryDirs,",
            "      else if (_platform.isLinux && !termuxLinuxArm64Host)\n"
            "        if (!termuxLinuxArm64Host) ..._linuxBinaryDirs,",
            1,
        )
    cache_file.write_text(source)
    cache_file.chmod(0o600)
    original_hash = hashlib.sha256(cache_file.read_bytes()).hexdigest()

    result = _run_flutter_cache_patch(cache_file, flutter_root, android_sdk, prefix)

    assert result.returncode == 0, result.stdout + result.stderr
    assert hashlib.sha256(cache_file.read_bytes()).hexdigest() != original_hash
    assert cache_file.stat().st_mode & 0o777 == 0o600
    patched = cache_file.read_text()
    assert "_platform.environment.containsKey('TERMUX_VERSION')" in patched
    assert "cache.getHostPlatformArchName() == 'arm64'" in patched
    assert patched.count("if (!termuxLinuxArm64Host) ..._linuxBinaryDirs") == 1 + guard_direct_spread
    expected_linux_spread = (
        "if (!termuxLinuxArm64Host) ..._linuxBinaryDirs,"
        if guard_direct_spread
        else "..._linuxBinaryDirs,"
    )
    assert "else if (_platform.isLinux && !termuxLinuxArm64Host)\n        " + expected_linux_spread in patched


def test_post_install_removes_only_arm64_snapshot_aliases(tmp_path):
    flutter_root, android_sdk, prefix, _ = create_mock_env(tmp_path)
    engine = flutter_root / "bin/cache/artifacts/engine"
    release = engine / "android-arm64-release"
    profile = engine / "android-arm64-profile"
    (release / "linux-arm64").mkdir(parents=True)
    (profile / "linux-arm64").mkdir(parents=True)
    (release / "linux-x64").symlink_to("linux-arm64")
    (profile / "linux-x64").mkdir()
    (engine / "linux-arm64").mkdir()
    unrelated = engine / "other-host"
    unrelated.mkdir()
    (engine / "linux-x64").symlink_to("other-host")

    result = run_post_install(flutter_root, android_sdk, prefix)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (release / "linux-x64").is_symlink()
    assert (profile / "linux-x64").is_dir() and not (profile / "linux-x64").is_symlink()
    assert (engine / "linux-x64").is_symlink()
    assert (engine / "linux-x64").resolve() == unrelated.resolve()


def test_pre_fix_x64_zip_overwrites_alias_but_cleanup_preserves_packaged_generators(tmp_path):
    flutter_root, android_sdk, prefix, _ = create_mock_env(tmp_path)
    engine = flutter_root / "bin/cache/artifacts/engine"
    arm64 = _synthetic_elf(183, "/system/bin/linker64", b"PACKAGED-ARM64")
    x64 = _synthetic_elf(62, "/lib64/ld-linux-x86-64.so.2", b"DOWNLOADED-X64")
    generator_paths = []

    malicious_zip = tmp_path / "linux-x64-snapshot.zip"
    with zipfile.ZipFile(malicious_zip, "w") as archive:
        for mode in ("release", "profile"):
            parent = engine / f"android-arm64-{mode}"
            (parent / "linux-arm64").mkdir(parents=True)
            generator = parent / "linux-arm64/gen_snapshot"
            generator.write_bytes(arm64)
            generator.chmod(0o755)
            (parent / "linux-x64").symlink_to("linux-arm64")
            archive.writestr(f"android-arm64-{mode}/linux-x64/gen_snapshot", x64)
            generator_paths.append(generator)

    # Reproduce the old writer: extracting the Linux x64 bundle through the host alias
    # overwrites the packaged ARM64 generator in place.
    with zipfile.ZipFile(malicious_zip) as archive:
        archive.extractall(engine)
    for generator in generator_paths:
        assert _elf_identity(generator)[0] == 62
        assert hashlib.sha256(generator.read_bytes()).hexdigest() != hashlib.sha256(arm64).hexdigest()

    # Restore the package bytes, then run the isolated installer cleanup and a legitimate
    # Android internal-artifact extraction. Neither action may mutate packaged generators.
    for generator in generator_paths:
        generator.write_bytes(arm64)
        generator.chmod(0o755)
    result = run_post_install(flutter_root, android_sdk, prefix)
    assert result.returncode == 0, result.stdout + result.stderr
    for mode in ("release", "profile"):
        assert not (engine / f"android-arm64-{mode}/linux-x64").is_symlink()

    android_zip = tmp_path / "android-internal-artifacts.zip"
    with zipfile.ZipFile(android_zip, "w") as archive:
        archive.writestr("android-arm64/artifacts/NOTICE", b"Android target artifacts")
    with zipfile.ZipFile(android_zip) as archive:
        archive.extractall(engine)

    for generator in generator_paths:
        assert generator.stat().st_mode & 0o111
        assert hashlib.sha256(generator.read_bytes()).hexdigest() == hashlib.sha256(arm64).hexdigest()
        assert _elf_identity(generator) == (183, "/system/bin/linker64")
    assert (engine / "android-arm64/artifacts/NOTICE").read_bytes() == b"Android target artifacts"


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
  final HostPlatform host;
  String getHostPlatformArchName() => host.platformName;
}
class HostPlatform {
  const HostPlatform(this.cliName, this.platformName);
  final String cliName;
  final String platformName;
}
class Platform {
  Platform({this.isWindows = false, this.isMacOS = false, this.isLinux = false, this.isTermux = false});
  final bool isWindows, isMacOS, isLinux;
  final bool isTermux;
  Map<String, String> get environment => isTermux ? {'TERMUX_VERSION': 'test'} : <String, String>{};
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
  const arm64 = HostPlatform('linux-arm64', 'arm64');
  const x64 = HostPlatform('linux-x64', 'x64');
  final armAll = AndroidGenSnapshotArtifacts(Cache(true, arm64), Platform(isLinux: true, isTermux: true)).getBinaryDirs();
  check(!armAll.any((entry) => entry.first == 'linux-host'), 'ARM64 includeAll selected Linux host binaries');
  check(!armAll.any((entry) => entry.first == 'linux-x64'), 'ARM64 includeAll selected x64 Dart SDK');
  check(armAll.any((entry) => entry.first == 'darwin-host'), 'includeAll lost Darwin bundles');
  check(armAll.any((entry) => entry.first == 'windows-host'), 'includeAll lost Windows bundles');
  final armLinux = AndroidGenSnapshotArtifacts(Cache(false, arm64), Platform(isLinux: true, isTermux: true)).getBinaryDirs();
  check(armLinux.isEmpty, 'ARM64 direct Linux selected incompatible bundles');
  final desktopArm64 = AndroidGenSnapshotArtifacts(Cache(false, arm64), Platform(isLinux: true)).getBinaryDirs();
  check(desktopArm64.any((entry) => entry.first == 'linux-host'), 'non-Termux ARM64 Linux behavior changed');
  final x64Linux = AndroidGenSnapshotArtifacts(Cache(false, x64), Platform(isLinux: true)).getBinaryDirs();
  check(x64Linux.any((entry) => entry.first == 'linux-host'), 'Linux x64 behavior changed');
  final windows = AndroidGenSnapshotArtifacts(Cache(false, const HostPlatform('windows-x64', 'x64')), Platform(isWindows: true)).getBinaryDirs();
  check(windows.any((entry) => entry.first == 'windows-host'), 'Windows behavior changed');
  print('patched Android snapshot selection: PASS');
}
"""
    )
    outcome = subprocess.run([dartvm, str(harness)], capture_output=True, text=True)
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr
    assert "patched Android snapshot selection: PASS" in outcome.stdout
