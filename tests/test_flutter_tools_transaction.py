"""Focused fault-injection tests for post-install FlutterTools publication."""

import os
import fcntl
import signal
import subprocess
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent
POST_INSTALL = REPO_ROOT / "scripts" / "install" / "post_install.sh"


def _finalizer_source():
    source = POST_INSTALL.read_text()
    start = source.index("finalize_flutter_tools_cache() {")
    end = source.index("\n}\n\nensure_profile_env", start) + 2
    return source[start:end]


@pytest.fixture
def sdk(tmp_path):
    root = tmp_path / "flutter"
    cache = root / "bin" / "cache"
    sdk_bin = cache / "dart-sdk" / "bin"
    tools = root / "packages" / "flutter_tools"
    (root / "bin" / "internal").mkdir(parents=True)
    sdk_bin.mkdir(parents=True)
    (tools / "bin").mkdir(parents=True)
    (tools / ".dart_tool").mkdir()
    (tools / "bin" / "flutter_tools.dart").write_text("void main() {}\n")
    (tools / ".dart_tool" / "package_config.json").write_text('{"configVersion": 2, "packages": []}\n')
    (tools / "pubspec.yaml").write_text("name: flutter_tools\n")
    (tools / "pubspec.lock").write_text("# generated\n")
    os.utime(tools / "pubspec.yaml", (1_600_000_000, 1_600_000_000))
    os.utime(tools / "pubspec.lock", (1_600_000_100, 1_600_000_100))
    (root / "bin" / "internal" / "shared.sh").write_text("# lock inode\n")
    (root / "bin" / "internal" / "engine.version").write_text("engine-revision\n")
    (cache / "engine.stamp").write_text("engine-revision\n")
    (cache / "engine_stamp.stamp").write_text("engine-revision\n")
    (cache / "engine_stamp.json").write_text(
        '{"build_time_ms": 1770000000000, "git_revision": "engine-revision", '
        '"git_revision_date": "2026-06-09T00:00:00Z", "content_hash": "engine-revision"}\n'
    )
    (root / "version").write_text("3.47.2\n")
    (root / "tmp").mkdir()
    dart = sdk_bin / "dart"
    dart.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, signal, sys, time, urllib.parse\n"
        "args=sys.argv[1:]\n"
        "if os.environ.get('MOCK_ARGS_LOG'): open(os.environ['MOCK_ARGS_LOG'], 'a').write(' '.join(args) + '\\n')\n"
        "out=next((a.split('=',1)[1] for a in args if a.startswith('--snapshot=')), None)\n"
        "if out is not None:\n"
        " mode=os.environ.get('MOCK_COMPILE','ok')\n"
        " if os.environ.get('MOCK_MUTATE_SOURCE'): pathlib.Path(os.environ['MOCK_MUTATE_SOURCE']).write_text('changed while compiling\\n')\n"
        " if mode == 'kill': os.kill(os.getppid(), signal.SIGKILL)\n"
        " if mode in ('int', 'term'): os.kill(os.getppid(), signal.SIGINT if mode == 'int' else signal.SIGTERM); time.sleep(.2)\n"
        " if mode == 'fail': sys.exit(17)\n"
        " pathlib.Path(out).write_bytes(b'KERNEL-MOCK-HEADER\\nvalid-tool')\n"
        " if mode == 'empty': pathlib.Path(out).write_bytes(b'')\n"
        " sys.exit(0)\n"
        "if os.environ.get('MOCK_VALIDATE_ISOLATION'):\n"
        " cfg=pathlib.Path(next(a.split('=',1)[1] for a in args if a.startswith('--packages=')))\n"
        " sdk=pathlib.Path(os.environ['FLUTTER_ROOT']).resolve()\n"
        " for package in json.loads(cfg.read_text())['packages']:\n"
        "  root=pathlib.Path(urllib.parse.unquote(urllib.parse.urlsplit(urllib.parse.urljoin(cfg.as_uri(),package['rootUri'])).path)).resolve()\n"
        "  assert root.is_relative_to(sdk) and root.is_dir() and not root.is_symlink()\n"
        "  assert all(not p.is_symlink() for p in root.rglob('*'))\n"
        "  if os.environ.get('MOCK_EXPECT_LINK'): assert (root/'linked_package.dart').read_text() == pathlib.Path(os.environ['MOCK_EXPECT_LINK']).read_text()\n"
        "snap=next((a for a in args if a.endswith('.snapshot')), None)\n"
        "if snap is None or pathlib.Path(snap).read_bytes() != b'KERNEL-MOCK-HEADER\\nvalid-tool': sys.exit(23)\n"
        "if os.environ.get('MOCK_REQUIRE_ENGINE_STAMP'):\n"
        " stamp=pathlib.Path(os.environ['FLUTTER_ROOT'])/'bin'/'cache'/'engine_stamp.json'\n"
        " data=json.loads(stamp.read_text())\n"
        " cache=stamp.parent\n"
        " assert (cache/'engine.stamp').read_text().strip() == data['git_revision'] == (cache/'engine_stamp.stamp').read_text().strip()\n"
        " assert data['content_hash'] and '--no-version-check' in args and '--suppress-analytics' in args and '--version' in args\n"
        "if os.environ.get('MOCK_LOAD') == 'fail': sys.exit(24)\n"
        "sys.exit(0)\n"
    )
    dart.chmod(0o755)
    return root, dart


def run_finalizer(root, dart, **extra_env):
    script = _finalizer_source() + "\nfinalize_flutter_tools_cache\n"
    env = {
        **os.environ,
        "FLUTTER_ROOT": str(root),
        "DART_SDK": str(dart.parent.parent),
        "PREFIX": str(root / "prefix"),
        "TMPDIR": str(root / "tmp"),
        "FLUTTER_PREBUILT_ENGINE_VERSION": "",
        "PATH": f"{dart.parent}:{os.environ['PATH']}",
        **extra_env,
    }
    return subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True)


def install_fault_hook(root, source):
    hook_dir = root / "tmp" / "python-hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "sitecustomize.py").write_text(source)
    return str(hook_dir)


def pair_paths(root):
    cache = root / "bin" / "cache"
    return cache / "flutter_tools.snapshot", cache / "flutter_tools.stamp"


def read_pair(root):
    return tuple(p.read_bytes() if p.exists() else None for p in pair_paths(root))


def test_publishes_valid_snapshot_and_matching_stamp(sdk):
    root, dart = sdk
    result = run_finalizer(root, dart)
    snapshot, stamp = pair_paths(root)
    assert result.returncode == 0, result.stderr
    assert snapshot.is_symlink() and stamp.is_symlink()
    assert snapshot.read_bytes() == b"KERNEL-MOCK-HEADER\nvalid-tool"
    assert stamp.read_text().strip() == "engine-revision:"
    assert snapshot.resolve().parent == stamp.resolve().parent


@pytest.mark.parametrize("mode", ["fail", "empty"])
def test_compile_failure_preserves_old_pair(sdk, mode):
    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    snapshot.write_bytes(b"old-snapshot")
    stamp.write_text("old-stamp\n")
    before = read_pair(root)
    result = run_finalizer(root, dart, MOCK_COMPILE=mode)
    assert result.returncode != 0
    assert read_pair(root) == before


def test_load_failure_preserves_old_pair(sdk):
    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    snapshot.write_bytes(b"old-snapshot")
    stamp.write_text("old-stamp\n")
    before = read_pair(root)
    result = run_finalizer(root, dart, MOCK_LOAD="fail")
    assert result.returncode != 0
    assert read_pair(root) == before


def test_missing_or_invalid_package_config_fails_closed(sdk):
    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    snapshot.write_bytes(b"old-snapshot")
    stamp.write_text("old-stamp\n")
    before = read_pair(root)
    config = root / "packages" / "flutter_tools" / ".dart_tool" / "package_config.json"
    config.unlink()
    result = run_finalizer(root, dart)
    assert result.returncode != 0
    assert read_pair(root) == before
    config.write_text("not-json\n")
    result = run_finalizer(root, dart)
    assert result.returncode != 0
    assert read_pair(root) == before


@pytest.mark.parametrize("mode", ["missing", "invalid"])
def test_missing_or_invalid_engine_stamp_fails_closed_without_touching_pair(sdk, mode):
    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    snapshot.write_bytes(b"old-snapshot")
    stamp.write_text("old-stamp\n")
    before = read_pair(root)
    engine_stamp = root / "bin" / "cache" / "engine_stamp.json"
    if mode == "missing":
        engine_stamp.unlink()
    else:
        engine_stamp.write_text("{}\n")
    result = run_finalizer(root, dart)
    assert result.returncode != 0
    assert "engine_stamp.json" in result.stderr
    assert read_pair(root) == before


def test_isolated_load_copies_engine_metadata_and_suppresses_network_update(sdk, tmp_path):
    root, dart = sdk
    log = tmp_path / "dart-args.log"
    result = run_finalizer(root, dart, MOCK_REQUIRE_ENGINE_STAMP="1", MOCK_ARGS_LOG=str(log))
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert len(calls) == 2
    assert "--no-version-check" in calls[1]
    assert "--suppress-analytics" in calls[1]
    assert "--version" in calls[1]


@pytest.mark.parametrize("existing", ["pair", "snapshot-only", "stamp-only", "neither"])
def test_failed_generation_retains_exact_preexisting_layout(sdk, existing):
    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    if existing in {"pair", "snapshot-only"}:
        snapshot.write_bytes(b"prior-snapshot")
    if existing in {"pair", "stamp-only"}:
        stamp.write_bytes(b"prior-stamp")
    before = read_pair(root)
    result = run_finalizer(root, dart, MOCK_COMPILE="fail")
    assert result.returncode != 0
    assert read_pair(root) == before


def test_exact_valid_pair_is_reused_without_content_or_metadata_change(sdk):
    root, dart = sdk
    first = run_finalizer(root, dart)
    assert first.returncode == 0, first.stderr
    snapshot, stamp = pair_paths(root)
    before = (snapshot.read_bytes(), stamp.read_bytes(), snapshot.stat(), stamp.stat())
    for _ in range(2):
        repeated = run_finalizer(root, dart, MOCK_COMPILE="fail")
        assert repeated.returncode == 0, repeated.stderr
    after = (snapshot.read_bytes(), stamp.read_bytes(), snapshot.stat(), stamp.stat())
    assert after[:2] == before[:2]
    assert (after[2].st_mode, after[2].st_uid, after[2].st_gid, after[2].st_mtime_ns) == (
        before[2].st_mode, before[2].st_uid, before[2].st_gid, before[2].st_mtime_ns
    )
    assert (after[3].st_mode, after[3].st_uid, after[3].st_gid, after[3].st_mtime_ns) == (
        before[3].st_mode, before[3].st_uid, before[3].st_gid, before[3].st_mtime_ns
    )


def test_managed_pair_rebuilds_on_flutter_tools_source_change_then_reuses(sdk, tmp_path):
    root, dart = sdk
    args_log = tmp_path / "compiler-args.log"
    first = run_finalizer(root, dart, MOCK_ARGS_LOG=str(args_log))
    assert first.returncode == 0, first.stderr
    snapshot, stamp = pair_paths(root)
    old_generation = (root / "bin" / "cache" / ".flutter_tools_current").readlink()
    old_stamp = stamp.read_bytes()
    before_calls = len(args_log.read_text().splitlines())

    entry = root / "packages" / "flutter_tools" / "bin" / "flutter_tools.dart"
    entry.write_text("void main() { print('patched source'); }\n")
    rebuilt = run_finalizer(root, dart, MOCK_ARGS_LOG=str(args_log))
    assert rebuilt.returncode == 0, rebuilt.stderr
    assert len(args_log.read_text().splitlines()) == before_calls + 2
    assert (root / "bin" / "cache" / ".flutter_tools_current").readlink() != old_generation
    assert stamp.read_bytes() == old_stamp, "the launcher stamp must remain exactly HEAD:FLUTTER_TOOL_ARGS"

    repeated = run_finalizer(root, dart, MOCK_COMPILE="fail")
    assert repeated.returncode == 0, repeated.stderr


def test_existing_valid_regular_appjit_pair_compiles_once_to_establish_manifest(sdk, tmp_path):
    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    snapshot.write_bytes(b"PREVIOUS-APP-JIT-SNAPSHOT")
    stamp.write_text("engine-revision:")
    log = tmp_path / "compiler-args.log"
    result = run_finalizer(root, dart, MOCK_ARGS_LOG=str(log))
    assert result.returncode == 0, result.stderr
    assert snapshot.is_symlink() and stamp.is_symlink()
    assert snapshot.read_bytes() == b"KERNEL-MOCK-HEADER\nvalid-tool"
    assert stamp.read_text() == "engine-revision:"
    assert len(log.read_text().splitlines()) == 2
    reused = run_finalizer(root, dart, MOCK_COMPILE="fail")
    assert reused.returncode == 0, reused.stderr


def test_tool_args_participate_in_stamp_and_compiler_invocation(sdk, tmp_path):
    root, dart = sdk
    log = tmp_path / "dart-args.log"
    result = run_finalizer(
        root, dart, FLUTTER_TOOL_ARGS="--enable-asserts --verbose", MOCK_ARGS_LOG=str(log)
    )
    snapshot, stamp = pair_paths(root)
    assert result.returncode == 0, result.stderr
    assert stamp.read_text() == "engine-revision:--enable-asserts --verbose"
    compile_args = log.read_text().splitlines()[0]
    assert "--enable-asserts" in compile_args
    assert "--verbose" in compile_args


def test_term_signal_during_compile_preserves_old_pair(sdk):
    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    snapshot.write_bytes(b"old-snapshot")
    stamp.write_text("old-stamp\n")
    before = read_pair(root)
    result = run_finalizer(root, dart, MOCK_COMPILE="term")
    assert result.returncode != 0
    assert read_pair(root) == before


def test_sigkill_during_compile_preserves_old_pair(sdk):
    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    snapshot.write_bytes(b"old-snapshot")
    stamp.write_text("old-stamp\n")
    before = read_pair(root)
    result = run_finalizer(root, dart, MOCK_COMPILE="kill")
    assert result.returncode != 0
    assert read_pair(root) == before


@pytest.mark.parametrize("mode", ["int", "term"])
def test_signal_during_compile_preserves_old_pair(sdk, mode):
    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    snapshot.write_bytes(b"old-snapshot")
    stamp.write_text("old-stamp\n")
    before = read_pair(root)
    result = run_finalizer(root, dart, MOCK_COMPILE=mode)
    assert result.returncode == (130 if mode == "int" else 143)
    assert read_pair(root) == before


def test_stale_pubspec_lock_fails_without_touching_old_pair(sdk):
    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    snapshot.write_bytes(b"old-snapshot")
    stamp.write_text("old-stamp\n")
    before = (snapshot.read_bytes(), stamp.read_bytes(), snapshot.stat(), stamp.stat())
    yaml = root / "packages" / "flutter_tools" / "pubspec.yaml"
    os.utime(yaml, (1_700_000_200, 1_700_000_200))
    result = run_finalizer(root, dart)
    assert result.returncode != 0
    assert "pubspec.lock is stale" in result.stderr
    assert snapshot.read_bytes() == before[0] and stamp.read_bytes() == before[1]
    for path, info in ((snapshot, before[2]), (stamp, before[3])):
        after = path.stat()
        assert (after.st_mode, after.st_uid, after.st_gid, after.st_mtime_ns) == (
            info.st_mode, info.st_uid, info.st_gid, info.st_mtime_ns
    )


def test_external_package_roots_are_copied_and_remapped_without_source_writes(sdk, tmp_path):
    import json

    root, dart = sdk
    external = tmp_path / "external-package"
    external.mkdir()
    source = external / "package.dart"
    source.write_text("class ExternalPackage {}\n")
    linked_source = external / "linked_package.dart"
    linked_source.symlink_to(source)
    source_stat = source.stat()
    config = root / "packages" / "flutter_tools" / ".dart_tool" / "package_config.json"
    config.write_text(json.dumps({"configVersion": 2, "packages": [{
        "name": "external_package", "rootUri": external.as_uri() + "/", "packageUri": "lib/"
    }]}) + "\n")
    result = run_finalizer(root, dart, MOCK_VALIDATE_ISOLATION="1", MOCK_EXPECT_LINK=str(linked_source))
    assert result.returncode == 0, result.stderr
    assert source.read_text() == "class ExternalPackage {}\n"
    assert linked_source.is_symlink(), "the external package source must remain untouched"
    after = source.stat()
    assert (after.st_mode, after.st_uid, after.st_gid, after.st_mtime_ns) == (
        source_stat.st_mode, source_stat.st_uid, source_stat.st_gid, source_stat.st_mtime_ns
    )
    isolated_cfg = next((root / "tmp").glob("flutter-tools-check-*/sdk/packages/flutter_tools/.dart_tool/package_config.json"), None)
    # The finalizer removes isolated trees on success; the mock validates their remapping during compilation.
    assert isolated_cfg is None


@pytest.mark.parametrize("root_uri", ["https://example.invalid/pkg/", "file:///missing/flutter-tools-package/"])
def test_non_file_or_missing_package_root_is_rejected(sdk, root_uri):
    import json

    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    snapshot.write_bytes(b"old-snapshot")
    stamp.write_text("old-stamp\n")
    config = root / "packages" / "flutter_tools" / ".dart_tool" / "package_config.json"
    config.write_text(json.dumps({"configVersion": 2, "packages": [{
        "name": "invalid_package", "rootUri": root_uri, "packageUri": "lib/"
    }]}) + "\n")
    result = run_finalizer(root, dart)
    assert result.returncode != 0
    assert read_pair(root) == (b"old-snapshot", b"old-stamp\n")


def test_sigkill_during_absent_pair_link_conversion_recovers_on_retry(sdk):
    root, dart = sdk
    marker = root / "tmp" / "first-public-link"
    hook = install_fault_hook(root, """
import os, signal
from pathlib import Path
original = os.replace
def kill_after_snapshot_link(src, dst):
    result = original(src, dst)
    if Path(dst).name == 'flutter_tools.snapshot' and Path(dst).parent.name == 'cache':
        Path(%r).write_text('converted')
        os.kill(os.getpid(), signal.SIGKILL)
    return result
os.replace = kill_after_snapshot_link
""" % str(marker))
    result = run_finalizer(root, dart, PYTHONPATH=hook)
    assert result.returncode != 0
    snapshot, stamp = pair_paths(root)
    assert marker.exists()
    assert snapshot.is_symlink() and not snapshot.exists()
    assert not stamp.exists()

    retry = run_finalizer(root, dart)
    assert retry.returncode == 0, retry.stderr
    assert snapshot.read_bytes() == b"KERNEL-MOCK-HEADER\nvalid-tool"
    assert stamp.read_text() == "engine-revision:"


@pytest.mark.parametrize("existing", ["stamp-only", "snapshot-only"])
def test_sigkill_during_partial_baseline_conversion_recovers_pair(sdk, existing):
    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    if existing == "snapshot-only":
        snapshot.write_bytes(b"old-snapshot")
    else:
        stamp.write_bytes(b"old-stamp")
    marker = root / "tmp" / "first-public-link"
    hook = install_fault_hook(root, """
import os, signal
from pathlib import Path
original = os.replace
def kill_after_snapshot_link(src, dst):
    result = original(src, dst)
    if Path(dst).name == 'flutter_tools.snapshot' and Path(dst).parent.name == 'cache':
        Path(%r).write_text('converted')
        os.kill(os.getpid(), signal.SIGKILL)
    return result
os.replace = kill_after_snapshot_link
""" % str(marker))
    crashed = run_finalizer(root, dart, PYTHONPATH=hook)
    assert crashed.returncode != 0 and marker.exists()
    retry = run_finalizer(root, dart)
    assert retry.returncode == 0, retry.stderr
    assert snapshot.read_bytes() == b"KERNEL-MOCK-HEADER\nvalid-tool"
    assert stamp.read_text() == "engine-revision:"


def test_stamp_write_failure_preserves_old_pair(sdk):
    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    snapshot.write_bytes(b"old-snapshot")
    stamp.write_text("old-stamp\n")
    before = read_pair(root)
    hook = install_fault_hook(root, """
from pathlib import Path
original = Path.write_bytes
def fail_staged_stamp(self, data):
    if self.name == 'flutter_tools.stamp' and self.parent.name.startswith('.stage-'):
        raise OSError('injected staged stamp write failure')
    return original(self, data)
Path.write_bytes = fail_staged_stamp
""")
    result = run_finalizer(root, dart, PYTHONPATH=hook)
    assert result.returncode != 0
    assert read_pair(root) == before


def test_input_manifest_write_failure_preserves_old_pair(sdk):
    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    snapshot.write_bytes(b"old-snapshot")
    stamp.write_text("old-stamp\n")
    before = read_pair(root)
    hook = install_fault_hook(root, """
from pathlib import Path
original = Path.write_text
def fail_staged_manifest(self, data, *args, **kwargs):
    if self.name == 'flutter_tools.inputs.sha256' and self.parent.name.startswith('.stage-'):
        raise OSError('injected staged input manifest write failure')
    return original(self, data, *args, **kwargs)
Path.write_text = fail_staged_manifest
""")
    result = run_finalizer(root, dart, PYTHONPATH=hook)
    assert result.returncode != 0
    assert read_pair(root) == before


def test_input_change_during_compile_prevents_publication(sdk):
    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    snapshot.write_bytes(b"old-snapshot")
    stamp.write_text("old-stamp\n")
    before = read_pair(root)
    entry = root / "packages" / "flutter_tools" / "bin" / "flutter_tools.dart"
    result = run_finalizer(root, dart, MOCK_MUTATE_SOURCE=str(entry))
    assert result.returncode != 0
    assert "inputs changed during staging" in result.stderr
    assert read_pair(root) == before


def test_public_link_rename_failure_preserves_old_pair(sdk):
    root, dart = sdk
    snapshot, stamp = pair_paths(root)
    snapshot.write_bytes(b"old-snapshot")
    stamp.write_text("old-stamp\n")
    before = read_pair(root)
    before_info = snapshot.stat()
    hook = install_fault_hook(root, """
import os
from pathlib import Path
original = os.replace
def fail_public_stamp(src, dst):
    if Path(dst).name == 'flutter_tools.stamp' and Path(dst).parent.name == 'cache':
        raise OSError('injected public-link rename failure')
    return original(src, dst)
os.replace = fail_public_stamp
""")
    result = run_finalizer(root, dart, PYTHONPATH=hook)
    assert result.returncode != 0
    assert read_pair(root) == before
    after_info = snapshot.stat()
    assert (after_info.st_mode, after_info.st_uid, after_info.st_gid, after_info.st_mtime_ns) == (
        before_info.st_mode, before_info.st_uid, before_info.st_gid, before_info.st_mtime_ns
    )


def test_sigkill_after_pointer_publish_keeps_complete_new_pair(sdk):
    root, dart = sdk
    marker = root / "tmp" / "pointer-published"
    hook_source = """
import os, time
from pathlib import Path
original = os.replace
count = 0
def pause_after_second_current(src, dst):
    global count
    result = original(src, dst)
    if Path(dst).name == '.flutter_tools_current':
        count += 1
        if count == 2:
            Path(%r).write_text('published')
            while True: time.sleep(1)
    return result
os.replace = pause_after_second_current
""" % str(marker)
    hook = install_fault_hook(root, hook_source)
    script = _finalizer_source() + "\nfinalize_flutter_tools_cache\n"
    env = {
        **os.environ,
        "FLUTTER_ROOT": str(root),
        "DART_SDK": str(dart.parent.parent),
        "PREFIX": str(root / "prefix"),
        "TMPDIR": str(root / "tmp"),
        "FLUTTER_PREBUILT_ENGINE_VERSION": "",
        "PATH": f"{dart.parent}:{os.environ['PATH']}",
        "PYTHONPATH": hook,
    }
    process = subprocess.Popen(["bash", "-c", script], env=env, start_new_session=True)
    deadline = time.monotonic() + 10
    while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert marker.exists(), "publisher did not reach the post-pointer fault point"
    os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=5)
    snapshot, stamp = pair_paths(root)
    assert snapshot.read_bytes() == b"KERNEL-MOCK-HEADER\nvalid-tool"
    assert stamp.read_text() == "engine-revision:"
    assert snapshot.resolve().parent == stamp.resolve().parent


def test_finalizer_waits_for_the_launcher_shared_file_lock(sdk):
    root, dart = sdk
    shared = root / "bin" / "internal" / "shared.sh"
    script = _finalizer_source() + "\nfinalize_flutter_tools_cache\n"
    env = {
        **os.environ,
        "FLUTTER_ROOT": str(root),
        "DART_SDK": str(dart.parent.parent),
        "PREFIX": str(root / "prefix"),
        "TMPDIR": str(root / "tmp"),
        "FLUTTER_PREBUILT_ENGINE_VERSION": "",
        "PATH": f"{dart.parent}:{os.environ['PATH']}",
        "MOCK_COMPILE": "fail",
    }
    with shared.open("rb") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        process = subprocess.Popen(["bash", "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(0.25)
        assert process.poll() is None, "finalizer must wait while launcher owns shared.sh lock"
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode != 0
    assert b"Timed out waiting" not in stderr
