"""Pytest fixtures and cross-platform test helpers for Flutter Termux SDK."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Union
import pytest

# Ensure repository root and tests directory are in sys.path
TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent


def _installed_dartvm(environment=None) -> str | None:
    """Resolve DartVM before pytest replaces HOME and other user directories."""
    environment = os.environ if environment is None else environment
    configured = environment.get("DARTVM")
    if configured:
        return str(Path(configured).expanduser().resolve())
    discovered = shutil.which("dartvm", path=environment.get("PATH"))
    if discovered:
        return str(Path(discovered).resolve())
    prefix = environment.get("PREFIX", "/data/data/com.termux/files/usr")
    installed = Path(prefix) / "opt/flutter/bin/cache/dart-sdk/bin/dartvm"
    return str(installed.resolve()) if installed.is_file() else None


_DARTVM = _installed_dartvm()


def isolated_user_env(state_root: Union[str, Path], base_env=None) -> dict[str, str]:
    """Return a subprocess environment with all user state and SDK roots test-local."""
    root = Path(state_root).resolve()
    home = root / "home"
    prefix = root / "prefix"
    flutter_root = root / "flutter-root"
    android_home = prefix / "opt" / "android-sdk"
    android_ndk = prefix / "opt" / "android-ndk"
    paths = {
        "TEST_USER_STATE_ROOT": root,
        "HOME": home,
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_CACHE_HOME": home / ".cache",
        "XDG_DATA_HOME": home / ".local/share",
        "XDG_STATE_HOME": home / ".local/state",
        "XDG_RUNTIME_DIR": home / ".runtime",
        "GRADLE_USER_HOME": home / ".gradle",
        "PUB_CACHE": home / ".pub-cache",
        "ANDROID_USER_HOME": home / ".android",
        "ANDROID_SDK_HOME": home / ".android-sdk-home",
        "ANDROID_AVD_HOME": home / ".android/avd",
        "PREFIX": prefix,
        "FLUTTER_ROOT": flutter_root,
        "DART_SDK": flutter_root / "bin" / "cache" / "dart-sdk",
        "ANDROID_HOME": android_home,
        "ANDROID_SDK_ROOT": android_home,
        "ANDROID_SDK": android_home,
        "ANDROID_NDK_HOME": android_ndk,
        "ANDROID_NDK_ROOT": android_ndk,
        "ANDROID_NDK": android_ndk,
        "NDK_ROOT": android_ndk,
        "TMPDIR": root / "tmp",
        "TMP": root / "tmp",
        "TEMP": root / "tmp",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    paths["XDG_RUNTIME_DIR"].chmod(0o700)
    env = os.environ.copy() if base_env is None else dict(base_env)

    # Keep native Termux utilities available, but never resolve Flutter from a
    # caller's installed SDK. Tests that need a fixture Flutter command install
    # an explicit mock in the fixture prefix.
    old_roots = []
    for value in (env.get("FLUTTER_ROOT"),):
        if value:
            old_roots.append(Path(value).expanduser().resolve())
    old_prefix = env.get("PREFIX")
    if old_prefix:
        old_roots.append((Path(old_prefix).expanduser() / "opt" / "flutter").resolve())
    filtered_path = []
    for entry in env.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        candidate = Path(entry).expanduser().resolve()
        if any(candidate == old_root or candidate.is_relative_to(old_root) for old_root in old_roots):
            continue
        filtered_path.append(entry)
    env.update({key: str(value) for key, value in paths.items()})
    env["PATH"] = os.pathsep.join(filtered_path)
    if env.get("DARTVM"):
        env["DARTVM"] = str(Path(env["DARTVM"]).expanduser().resolve())
    elif _DARTVM:
        env["DARTVM"] = _DARTVM
    return env


@pytest.fixture(autouse=True)
def isolate_test_user_state(tmp_path_factory, monkeypatch):
    """Prevent tests and children from using caller caches or installed SDK roots."""
    env = isolated_user_env(tmp_path_factory.mktemp("pytest-user-state"))
    for key in (
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "GRADLE_USER_HOME",
        "PUB_CACHE",
        "ANDROID_USER_HOME",
        "ANDROID_SDK_HOME",
        "ANDROID_AVD_HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "TEST_USER_STATE_ROOT",
        "PATH",
        "PREFIX",
        "FLUTTER_ROOT",
        "DART_SDK",
        "ANDROID_HOME",
        "ANDROID_SDK_ROOT",
        "ANDROID_SDK",
        "ANDROID_NDK_HOME",
        "ANDROID_NDK_ROOT",
        "ANDROID_NDK",
        "NDK_ROOT",
        "DARTVM",
    ):
        if key in env:
            monkeypatch.setenv(key, env[key])

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

_IS_WSL_BASH = None


def is_wsl_bash() -> bool:
    """Dynamically detect if 'bash' command executes WSL bash or MSYS2/Git-Bash."""
    global _IS_WSL_BASH
    if _IS_WSL_BASH is not None:
        return _IS_WSL_BASH
    if sys.platform != "win32":
        _IS_WSL_BASH = False
        return False
    try:
        res = subprocess.run(["bash", "-c", "echo $WSL_DISTRO_NAME"], capture_output=True, text=True, timeout=3)
        if res.stdout.strip():
            _IS_WSL_BASH = True
            return True
        res2 = subprocess.run(["bash", "-c", "uname -r"], capture_output=True, text=True, timeout=3)
        _IS_WSL_BASH = "microsoft" in res2.stdout.lower() or "wsl" in res2.stdout.lower()
    except Exception:
        _IS_WSL_BASH = False
    return _IS_WSL_BASH


def to_bash_path(path: Union[str, Path]) -> str:
    """Convert a file path to the appropriate POSIX path format for the detected bash environment.

    - On Windows with WSL bash: C:\\foo\\bar -> /mnt/c/foo/bar
    - On Windows with Git-Bash/MSYS2: C:\\foo\\bar -> /c/foo/bar
    - On Linux/macOS: /foo/bar -> /foo/bar
    """
    p = Path(path).resolve()
    if sys.platform == "win32":
        if p.drive:
            drive = p.drive[0].lower()
            rel = p.as_posix()[len(p.drive):]  # strip 'C:' or 'D:'
            if is_wsl_bash():
                return f"/mnt/{drive}{rel}"
            else:
                return f"/{drive}{rel}"
        return p.as_posix()
    return p.as_posix()


to_wsl_posix = to_bash_path
to_bash_posix = to_bash_path


# chrome.dart needs more than a one-line stub: post_install's patch_chrome anchors on the
# kLinuxExecutable declaration, on the `return kLinuxExecutable;` it replaces, and on the two doc
# comments it inserts before. A stub without them is unpatchable and post_install must abort on it,
# so every mock environment builds this fixture instead.
CHROME_FIXTURE = (
    "const kLinuxExecutable = 'google-chrome';\n"
    "\n"
    "String findChromeExecutable(Platform platform, FileSystem fileSystem) {\n"
    "  if (platform.isLinux) {\n"
    "    return kLinuxExecutable;\n"
    "  }\n"
    "  throwToolExit('Platform ${platform.operatingSystem} is not supported.');\n"
    "}\n"
    "\n"
    "/// The expected executable name on macOS.\n"
    "const kMacOSExecutable = '/Applications/Google Chrome.app';\n"
    "\n"
    "/// Find the Microsoft Edge executable on the current platform.\n"
    "String findEdgeExecutable(Platform platform, FileSystem fileSystem) {\n"
    "  return '';\n"
    "}\n"
)

# The state an install already on the released deb carries: the previous patch_chrome only added
# the Android lookup, never the candidate list that makes `chromium-browser` discoverable.
CHROME_LEGACY_PREIMAGE = CHROME_FIXTURE.replace(
    "  if (platform.isLinux) {",
    "  if (platform.isLinux || platform.isAndroid) { // Termux: use Linux Chrome lookup on Android host.",
)

# Marker that proves patch_chrome upgraded a file to the current postimage.
CHROME_CANDIDATES_MARKER = "kLinuxExecutableCandidates"
