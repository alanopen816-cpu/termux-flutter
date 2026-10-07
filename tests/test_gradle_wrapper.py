import os
from pathlib import Path
import shutil
import subprocess

import pytest

from conftest import isolated_user_env

REPO_ROOT = Path(__file__).resolve().parents[1]
POST_INSTALL = REPO_ROOT / "scripts/install/post_install.sh"
REPACK = REPO_ROOT / "scripts/package/repack_verified_payload.py"


def _apply_patch(target, state_root):
    env = isolated_user_env(state_root)
    env.update({"TARGET": str(target), "POST_INSTALL_SCRIPT": str(POST_INSTALL)})
    return subprocess.run(
        ["bash", "-c", 'source "$POST_INSTALL_SCRIPT" --lib && patch_gradle_wrapper "$TARGET"'],
        env=env,
        capture_output=True,
        text=True,
    )


def _patch_fixture(tmp_path):
    target = tmp_path / "gradle_utils.dart"
    target.write_text(
        "class GradleUtils {\n"
        "  void injectGradleWrapperIfNeeded() {\n"
        "    onFileCopied: (File source, File dest) {\n"
        "        _operatingSystemUtils.makeExecutable(dest);\n"
        "    },\n"
        "  }\n"
        "}\n"
    )
    return target, _apply_patch(target, tmp_path / "env")


def _extract_dart_function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError("unterminated Dart helper")


def test_installer_and_repacker_patch_new_gradle_wrapper_source(tmp_path):
    target, result = _patch_fixture(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    patched = target.read_text()
    assert "void _termuxNormalizeGradleWrapper(File file, Platform platform)" in patched
    assert "_termuxNormalizeGradleWrapper(dest, _platform);" in patched
    assert "TERMUX_VERSION" in patched
    assert "file.parent.createTempSync('.termux-gradlew-')" in patched
    assert "renameSync(file.path)" in patched
    assert "File('${file.path}" not in patched
    before = target.read_bytes()
    result = _apply_patch(target, tmp_path / "env-repeat")
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_bytes() == before
    repack_source = REPACK.read_text()
    assert '("patch_gradle_wrapper", "packages/flutter_tools/lib/src/android/gradle_utils.dart")' in repack_source


def test_gradle_wrapper_patch_rejects_unknown_layout_without_rewriting(tmp_path):
    target = tmp_path / "gradle_utils.dart"
    original = b"class GradleUtils {\n  void changedLayout() {}\n}\n"
    target.write_bytes(original)
    env = isolated_user_env(tmp_path / "env")
    env.update({"TARGET": str(target), "POST_INSTALL_SCRIPT": str(POST_INSTALL)})
    result = subprocess.run(
        ["bash", "-c", 'source "$POST_INSTALL_SCRIPT" --lib && patch_gradle_wrapper "$TARGET"'],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert target.read_bytes() == original


def test_wrapper_helper_preserves_custom_and_non_termux_wrappers_and_rejects_malformed(tmp_path):
    dartvm = os.environ.get("DARTVM") or shutil.which("dartvm")
    if not dartvm:
        pytest.skip("DARTVM is required to execute the extracted wrapper helper")
    target, result = _patch_fixture(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    helper = _extract_dart_function(
        target.read_text(), "void _termuxNormalizeGradleWrapper(File file, Platform platform)"
    )
    # This fixture models package:file's abstract File/Directory interfaces. Using
    # dart:io File as the helper's File type would miss the real compile failure.
    helper_prefix = r"""import 'dart:io' as io;
abstract class File {
  String get path;
  Directory get parent;
  bool existsSync();
  String readAsStringSync();
  void writeAsStringSync(String contents, {bool flush = false});
  File renameSync(String newPath);
}
abstract class Directory {
  Directory createTempSync([String? prefix]);
  File childFile(String name);
  bool existsSync();
  void deleteSync({bool recursive = false});
}
class LocalDirectory implements Directory {
  LocalDirectory(this.delegate);
  final io.Directory delegate;
  Directory createTempSync([String? prefix]) => LocalDirectory(delegate.createTempSync(prefix));
  File childFile(String name) => LocalFile(io.File('${delegate.path}/$name'));
  bool existsSync() => delegate.existsSync();
  void deleteSync({bool recursive = false}) => delegate.deleteSync(recursive: recursive);
}
class LocalFile implements File {
  LocalFile(this.delegate);
  final io.File delegate;
  static bool failWrite = false;
  static bool failRename = false;
  String get path => delegate.path;
  Directory get parent => LocalDirectory(delegate.parent);
  bool existsSync() => delegate.existsSync();
  String readAsStringSync() => delegate.readAsStringSync();
  void writeAsStringSync(String contents, {bool flush = false}) {
    if (failWrite) throw StateError('injected write failure');
    delegate.writeAsStringSync(contents, flush: flush);
  }
  File renameSync(String newPath) {
    if (failRename) throw StateError('injected rename failure');
    return LocalFile(delegate.renameSync(newPath));
  }
}
class Platform { Platform(this.isLinux, this.environment); final bool isLinux; final Map<String, String> environment; }
""" + helper

    def run(contents, *, termux=True, prefix="/private/usr", linux=True, failure=None):
        wrapper = tmp_path / "gradlew"
        wrapper.write_text(contents)
        baseline = wrapper.stat()
        environment = {"TERMUX_VERSION": "test"} if termux else {}
        environment["PREFIX"] = prefix
        helper_test = tmp_path / "wrapper_helper_test.dart"
        helper_test.write_text(
            helper_prefix
            + "\nvoid main() {\n"
            + f"  LocalFile.failWrite = {str(failure == 'write').lower()};\n"
            + f"  LocalFile.failRename = {str(failure == 'rename').lower()};\n"
            + f"  final File file = LocalFile(io.File({__import__('json').dumps(str(wrapper))}));\n"
            + f"  _termuxNormalizeGradleWrapper(file, Platform({str(linux).lower()}, {environment!r}));\n"
            + "}\n"
        )
        env = isolated_user_env(tmp_path / "runtime-env")
        result = subprocess.run([dartvm, str(helper_test)], env=env, capture_output=True, text=True)
        return wrapper, result, baseline

    wrapper, result, _ = run("#!/usr/bin/env bash\necho ok\n")
    assert result.returncode == 0, result.stdout + result.stderr
    expected = "#!/private/usr/bin/bash\necho ok\n"
    assert wrapper.read_text() == expected
    wrapper, result, _ = run(expected)
    assert result.returncode == 0, result.stdout + result.stderr
    assert wrapper.read_text() == expected
    wrapper, result, _ = run("#!/custom/bash\necho custom\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert wrapper.read_text() == "#!/custom/bash\necho custom\n"
    wrapper, result, _ = run("#!/usr/bin/env bash\necho unchanged\n", termux=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert wrapper.read_text() == "#!/usr/bin/env bash\necho unchanged\n"
    wrapper, result, _ = run("#!/usr/bin/env bash\necho unchanged\n", linux=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert wrapper.read_text() == "#!/usr/bin/env bash\necho unchanged\n"
    wrapper, result, baseline = run("#!/usr/bin/env bash")
    assert result.returncode != 0
    assert wrapper.read_bytes() == b"#!/usr/bin/env bash"
    wrapper, result, baseline = run("#!/usr/bin/env bash\necho ok\n", prefix="")
    assert result.returncode != 0
    assert wrapper.read_text() == "#!/usr/bin/env bash\necho ok\n"
    for failure in ("write", "rename"):
        wrapper, result, baseline = run("#!/usr/bin/env bash\necho ok\n", failure=failure)
        assert result.returncode != 0
        assert wrapper.read_text() == "#!/usr/bin/env bash\necho ok\n"
        after = wrapper.stat()
        assert after.st_mode == baseline.st_mode
        assert after.st_mtime_ns == baseline.st_mtime_ns
        assert not list(tmp_path.glob(".termux-gradlew-*"))
