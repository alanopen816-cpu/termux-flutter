import hashlib
import importlib.util
import json
import errno
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts/package/repack_verified_payload.py"
_SPEC = importlib.util.spec_from_file_location("repack_verified_payload", _SCRIPT)
repack = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(repack)


def test_source_archive_sha_is_pinned_before_any_extraction(tmp_path, monkeypatch):
    source = tmp_path / "source.deb"
    source.write_bytes(b"not the verified archive")
    monkeypatch.setattr(repack, "_extract_archive", lambda *_args: pytest.fail("must verify first"))
    repo = tmp_path / "repo"
    repo.mkdir()

    with pytest.raises(repack.RepackError, match="SHA-256"):
        repack.repack(source, repo / "artifacts/install-ready/output.deb", repo_root=repo)
    assert not (repo / "artifacts").exists()


def test_input_and_output_collision_or_existing_output_are_rejected(tmp_path):
    source = tmp_path / "source.deb"
    source.write_bytes(b"source")
    output = tmp_path / "output.deb"
    output.write_bytes(b"existing")

    with pytest.raises(repack.RepackError, match="already exists"):
        repack.validate_paths(source, output)
    with pytest.raises(repack.RepackError, match="same file"):
        repack.validate_paths(source, source)


def test_package_config_normalizes_contained_roots_and_rejects_escapes(tmp_path):
    root = tmp_path / "flutter"
    package_root = root / "packages/example"
    package_root.mkdir(parents=True)
    config = root / "packages/flutter_tools/.dart_tool/package_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"configVersion": 2, "packages": [
        {"name": "flutter_tools", "rootUri": "../"},
        {"name": "example", "rootUri": "../../example/"},
    ]}))

    roots = repack.normalize_package_config(root, config)

    assert roots == [(root / "packages/flutter_tools").resolve(), package_root.resolve()]
    normalized = json.loads(config.read_text())
    assert normalized["packages"][1]["rootUri"] == "../../example/"

    config.write_text(json.dumps({"configVersion": 2, "packages": [
        {"name": "flutter_tools", "rootUri": "../"},
        {"name": "escape", "rootUri": "%2e%2e/%2e%2e/%2e%2e/outside/"},
    ]}))
    with pytest.raises(repack.RepackError, match="escapes or is missing"):
        repack.normalize_package_config(root, config)


def test_package_config_rebases_only_pinned_archive_ci_cache_prefix(tmp_path):
    root = tmp_path / "flutter"
    tools = root / "packages/flutter_tools"
    cache = root / ".pub-cache/hosted/pub.dev"
    config = tools / ".dart_tool/package_config.json"
    config.parent.mkdir(parents=True)
    package = cache / "analyzer-10.1.0"
    package.mkdir(parents=True)
    source_prefix = "/home/runner/work/termux-flutter/termux-flutter/flutter"
    config.write_text(json.dumps({"configVersion": 2, "packages": [
        {"name": "flutter_tools", "rootUri": "../"},
        {"name": "analyzer", "rootUri":
         f"file://{source_prefix}/.pub-cache/hosted/pub.dev/analyzer-10.1.0"},
    ]}))

    roots = repack.normalize_package_config(root, config)

    assert roots == [tools.resolve(), package.resolve()]
    normalized = json.loads(config.read_text())
    assert normalized["packages"][0]["rootUri"] == "../"
    assert normalized["packages"][1]["rootUri"] == "../../../.pub-cache/hosted/pub.dev/analyzer-10.1.0/"


def test_package_config_rejects_nonmatching_ci_prefix_and_escaped_absolute_root(tmp_path):
    root = tmp_path / "flutter"
    tools = root / "packages/flutter_tools"
    (root / ".pub-cache/hosted/pub.dev/analyzer").mkdir(parents=True)
    config = tools / ".dart_tool/package_config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"configVersion": 2, "packages": [
        {"name": "flutter_tools", "rootUri": "../"},
        {"name": "analyzer", "rootUri":
         "file:///home/runner/work/termux-flutter/termux-flutter/flutter/.pub-cache/hosted/pub.dev/analyzer"},
        {"name": "external", "rootUri": "file:///unrelated/flutter/.pub-cache/hosted/pub.dev/pkg"},
    ]}))
    with pytest.raises(repack.RepackError, match="escapes|source prefix|traversal"):
        repack.normalize_package_config(root, config)

    config.write_text(json.dumps({"configVersion": 2, "packages": [
        {"name": "flutter_tools", "rootUri": "../"},
        {"name": "escape", "rootUri":
         "file:///home/runner/work/termux-flutter/termux-flutter/flutter/%2e%2e/outside"},
    ]}))
    with pytest.raises(repack.RepackError, match="escapes|source prefix|traversal"):
        repack.normalize_package_config(root, config)


def test_deb_identity_reads_real_dpkg_control_fields(tmp_path):
    package = tmp_path / "package"
    control = package / "DEBIAN/control"
    control.parent.mkdir(parents=True)
    control.parent.chmod(0o755)
    control.write_text("Package: flutter\nVersion: 3.47.2-1\nArchitecture: all\nMaintainer: test\nDescription: fixture\n")
    deb = tmp_path / "fixture.deb"
    subprocess.run(["dpkg-deb", "--build", str(package), str(deb)], check=True, capture_output=True, text=True)

    labelled = subprocess.run(["dpkg-deb", "-f", str(deb), "Package", "Version", "Architecture"],
                              check=True, capture_output=True, text=True).stdout.splitlines()
    assert labelled == ["Package: flutter", "Version: 3.47.2-1", "Architecture: all"]
    assert repack.read_deb_identity(deb) == ("flutter", "3.47.2-1", "all")


def test_engine_json_must_match_archived_revision_and_have_required_fields(tmp_path):
    version = tmp_path / "engine.version"
    stamp = tmp_path / "engine_stamp.stamp"
    metadata = tmp_path / "engine_stamp.json"
    revision = "a" * 40
    version.write_text(revision + "\n")
    stamp.write_text(revision)
    metadata.write_text(json.dumps({
        "git_revision": revision,
        "build_time_ms": 1,
        "content_hash": "verified-content",
        "git_revision_date": "2026-06-09T00:00:00Z",
    }))

    assert repack.validate_engine_metadata(version, stamp, metadata) == revision

    metadata.write_text(json.dumps({"git_revision": "b" * 40}))
    with pytest.raises(repack.RepackError, match="engine metadata"):
        repack.validate_engine_metadata(version, stamp, metadata)


def test_native_inventory_comparison_requires_identical_bytes(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    old = before / "dart"
    new = after / "dart"
    elf = b"\x7fELF" + b"payload"
    old.write_bytes(elf)
    new.write_bytes(elf)

    assert repack.native_inventory(before) == repack.native_inventory(after)
    new.write_bytes(elf + b"changed")
    with pytest.raises(repack.RepackError, match="native payload changed"):
        repack.assert_native_inventory_equal(before, after)


def test_repack_resources_preserve_full_engine_cache_native_inventory(tmp_path):
    import yaml

    root = tmp_path / "flutter"
    (root / "bin/internal").mkdir(parents=True)
    (root / "bin/internal/engine.version").write_text("a" * 40)
    (root / "bin/version").write_text("3.47.2\n")
    cache = root / "bin/cache/artifacts/engine"
    release = cache / "android-arm64-release/linux-arm64/gen_snapshot"
    profile = cache / "android-arm64-profile/linux-arm64/gen_snapshot"
    companion = cache / "android-arm64-release/linux-arm64/libpayload.so"
    for path, contents in ((release, b"\x7fELFrelease"), (profile, b"\x7fELFprofile"),
                           (companion, b"\x7fELFengine-library")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        path.chmod(0o755)
    extra = root / "bin/cache/dart-sdk/bin/dart"
    extra.parent.mkdir(parents=True)
    extra.write_bytes(b"\x7fELFdart-runtime")
    (root / "bin/cache/flutter_tools_git").mkdir(parents=True)
    (root / "bin/cache/flutter_tools_git/termux_synthetic").write_text("fixture\n")

    package_yaml = yaml.safe_load((repack.REPO_ROOT / "package.yaml").read_text(encoding="utf-8"))
    package = repack._package_object(root, repack.REPO_ROOT, package_yaml)

    class GenericTreeOnly:
        def gen_resource(self, _names):
            yield from package.gen_resource("flutter")

    with pytest.raises(repack.RepackError, match="do not preserve staged native ELF inventory"):
        repack.validate_package_native_inventory(GenericTreeOnly(), root)

    resources = list(package.gen_resource(list(repack.PUBLISHED_RESOURCES)))
    outputs = {Path(item["out"]) for item in resources}
    expected_outputs = {
        repack.FLUTTER_RELATIVE / release.relative_to(root),
        repack.FLUTTER_RELATIVE / profile.relative_to(root),
        repack.FLUTTER_RELATIVE / companion.relative_to(root),
    }
    assert expected_outputs <= outputs, "resource filtering dropped cached release/profile engine payload"
    assert repack.validate_package_native_inventory(package, root) == repack.native_inventory(root)


def test_precompression_manifest_requires_every_package_member_route(tmp_path):
    import build
    import package as package_builder

    root = tmp_path / "flutter"
    flutter_tools = root / "packages/flutter_tools"
    (flutter_tools / ".dart_tool").mkdir(parents=True)
    (flutter_tools / "pubspec.yaml").write_text("name: flutter_tools\n")
    pub_cache_root = root / ".pub-cache/hosted/pub.dev/example-1.0.0"
    (pub_cache_root / "lib/src").mkdir(parents=True)
    (pub_cache_root / "lib/src/example.dart").write_text("// package source")
    roots = [flutter_tools, pub_cache_root]
    for relative in build.REQUIRED_DEB_TOOL_ARTIFACTS:
        path = root / relative.removeprefix("opt/flutter/")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(b"required payload")
    git_cache = root / "bin/cache/flutter_tools_git"
    (git_cache / "refs/heads").mkdir(parents=True)
    (git_cache / "HEAD").write_text("ref: refs/heads/stable\n")
    (git_cache / "refs/heads/stable").write_text("a" * 40 + "\n")
    (git_cache / "termux_synthetic").write_text("fixture\n")
    shutil.copytree(git_cache, root / ".git", dirs_exist_ok=True)

    generic = package_builder.Package(
        root=root, arch="arm64", tag="3.47.2", revision="1",
        control={"Package": "flutter", "Version": "3.47.2-1", "Architecture": "aarch64",
                 "Maintainer": "test", "Description": "test"},
        resource={"flutter": {"source": str(root), "output": str(repack.FLUTTER_RELATIVE)}},
        maintainer_scripts={"postinst": root.parent / "postinst"},
    )
    (root.parent / "postinst").write_text("#!/bin/sh\nexit 0\n")
    (root.parent / "postinst").chmod(0o755)

    class FixtureRoutes:
        omit_pub_cache = False

        def gen_resource(self, _names):
            for item in generic.gen_resource("flutter"):
                if self.omit_pub_cache and ".pub-cache/" in str(item["out"]):
                    continue
                yield item
            for item in generic.gen_resource("flutter"):
                source = item.get("src")
                if isinstance(source, Path) and "bin/cache/flutter_tools_git/" in str(source):
                    relative = source.relative_to(git_cache)
                    yield {"out": repack.FLUTTER_RELATIVE / ".git" / relative, "src": source}
            for suffix in build.REQUIRED_DEB_INSTALLER_ARTIFACTS:
                yield {"out": repack.PREFIX_RELATIVE / suffix, "src": b"#!/bin/sh\n",
                       "mod": 0o755}

        def gen_control_files(self):
            return generic.gen_control_files()

    routes = FixtureRoutes()
    manifest = repack.validate_package_payload_routes(routes, root, roots)
    assert manifest["package_root_count"] == 2
    assert manifest["package_root_file_count"] >= 4
    assert len(manifest["package_root_members_sha256"]) == 64

    routes.omit_pub_cache = True
    with pytest.raises(repack.RepackError, match="package payload route is missing"):
        repack.validate_package_payload_routes(routes, root, roots)


def test_resource_overlay_targets_payload_root_not_working_directory(tmp_path):
    payload = tmp_path / "payload"
    sdk = payload / repack.FLUTTER_RELATIVE
    sdk.mkdir(parents=True)
    expected = payload / "data/data/com.termux/files/usr/opt/flutter/bin/cache/flutter.version.json"

    repack._apply_overlay(payload, [{"out": expected.relative_to(payload), "src": b'{"flutterVersion":"3.47.2"}', "mod": 0o644}])

    assert expected.read_bytes() == b'{"flutterVersion":"3.47.2"}'


def test_repack_temp_and_output_cannot_target_home_or_installed_sdk(tmp_path, monkeypatch):
    home = tmp_path / "home"
    prefix = tmp_path / "prefix"
    repo = tmp_path / "repo"
    home.mkdir()
    prefix.mkdir()
    repo.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PREFIX", str(prefix))

    with pytest.raises(repack.RepackError, match="temporary workspace"):
        repack._validate_scratch_locations(home / "scratch", repo / "out.deb", repo)
    with pytest.raises(repack.RepackError, match="installed SDK"):
        repack._validate_scratch_locations(tmp_path / "scratch", prefix / "opt/flutter/out.deb", repo)


def test_synthetic_git_compile_key_is_deterministic_and_marked(tmp_path):
    revisions = []
    for index in range(2):
        root = tmp_path / f"flutter-{index}"
        (root / "bin/internal").mkdir(parents=True)
        (root / "bin/cache").mkdir(parents=True)
        (root / "bin/flutter").write_text("#!/bin/sh\nexit 0\n")
        (root / "bin/flutter").chmod(0o755)
        (root / "bin/internal/engine.version").write_text("a" * 40)
        (root / "bin/internal/release-candidate-branch.version").write_text("3.47.2\n")

        revision = repack._synthetic_git(root, private=tmp_path / f"git-private-{index}")

        revisions.append(revision)
        assert (root / ".git/termux_synthetic").is_file()
        assert (root / "bin/cache/flutter_tools_git/termux_synthetic").is_file()
        assert (root / ".git/refs/heads/stable").read_text().strip() == revision
    assert revisions[0] == revisions[1]


def test_invalid_final_build_is_never_published(tmp_path):
    staged = tmp_path / "staged.deb"
    staged.write_bytes(b"candidate")
    output = tmp_path / "output.deb"

    with pytest.raises(RuntimeError, match="compile/load failed"):
        repack.publish_verified(staged, output, lambda _path: (_ for _ in ()).throw(
            RuntimeError("compile/load failed")))
    assert not output.exists()


def test_verified_publication_atomically_preserves_candidate_bytes_and_mode(tmp_path):
    candidate = tmp_path / "candidate.deb"
    output = tmp_path / "published.deb"
    candidate.write_bytes(b"complete validated package")
    candidate.chmod(0o640)
    validated = []

    repack.publish_verified(candidate, output, lambda path: validated.append(path.read_bytes()))

    assert validated == [candidate.read_bytes()]
    assert output.read_bytes() == candidate.read_bytes()
    assert output.stat().st_mode & 0o777 == 0o640
    assert not list(tmp_path.glob(".published.deb.*.pending"))


@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
def test_verified_publication_never_overwrites_existing_path(tmp_path, kind):
    candidate = tmp_path / "candidate.deb"
    output = tmp_path / "published.deb"
    candidate.write_bytes(b"new")
    if kind == "file":
        output.write_bytes(b"keep")
    elif kind == "directory":
        output.mkdir()
    else:
        target = tmp_path / "symlink-target"
        target.write_bytes(b"keep")
        output.symlink_to(target)

    with pytest.raises(repack.RepackError, match="already exists"):
        repack.publish_verified(candidate, output, lambda _path: None)

    if kind == "file":
        assert output.read_bytes() == b"keep"
    elif kind == "directory":
        assert output.is_dir()
    else:
        assert output.is_symlink() and output.read_bytes() == b"keep"


def test_rename_noreplace_wins_one_concurrent_publication_race(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    target = tmp_path / "target"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    barrier = threading.Barrier(3)
    results = []

    def rename(source):
        barrier.wait()
        try:
            repack._rename_noreplace(source, target)
            results.append((source, 0))
        except FileExistsError as exc:
            results.append((source, exc.errno))

    threads = [threading.Thread(target=rename, args=(source,)) for source in (first, second)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sum(status == 0 for _, status in results) == 1
    assert sum(status == errno.EEXIST for _, status in results) == 1
    assert target.read_bytes() in (b"first", b"second")
    loser = first if first.exists() else second
    assert loser.exists()


@pytest.mark.parametrize("kind", ["file", "directory", "symlink"])
def test_rename_noreplace_rejects_existing_filesystem_object(tmp_path, kind):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.write_bytes(b"new candidate")
    if kind == "file":
        destination.write_bytes(b"keep existing")
    elif kind == "directory":
        destination.mkdir()
    else:
        target = tmp_path / "target"
        target.write_bytes(b"keep existing")
        destination.symlink_to(target)

    with pytest.raises(FileExistsError) as error:
        repack._rename_noreplace(source, destination)

    assert error.value.errno == errno.EEXIST
    assert source.read_bytes() == b"new candidate"
    if kind == "file":
        assert destination.read_bytes() == b"keep existing"
    elif kind == "directory":
        assert destination.is_dir()
    else:
        assert destination.is_symlink() and destination.read_bytes() == b"keep existing"


def test_atomic_publisher_preflight_and_unsupported_primitive_cleanup(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    repack.preflight_atomic_publisher(scratch)
    assert list(scratch.iterdir()) == []

    candidate = tmp_path / "candidate.deb"
    candidate.write_bytes(b"candidate")
    output = tmp_path / "published.deb"
    monkeypatch.setattr(repack, "_rename_noreplace", lambda *_args: (_ for _ in ()).throw(
        repack.RepackError("renameat2 unavailable")))
    with pytest.raises(repack.RepackError, match="renameat2 unavailable"):
        repack.publish_verified(candidate, output, lambda _path: None)
    assert not output.exists()
    assert not list(tmp_path.glob(".published.deb.*.pending"))


def test_repack_compile_failure_leaves_requested_output_absent(tmp_path, monkeypatch):
    source = tmp_path / "source.deb"
    source.write_bytes(b"pinned-test-source")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(repack, "SOURCE_SHA256", digest)
    source.with_name("build_metadata.json").write_text(json.dumps({
        "sha256": digest, "version": "3.47.2", "arch": "arm64", "source_commit": "archive-revision",
    }))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.yaml").write_text("control: {}\nresource: {}\n")
    output = repo / "artifacts/install-ready/flutter_3.47.2_aarch64.deb"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setenv("TMPDIR", str(scratch))

    def extract(_source, payload, _control):
        root = payload / repack.FLUTTER_RELATIVE
        cache = root / "bin/cache"
        tools = root / "packages/flutter_tools"
        (root / "bin/internal").mkdir(parents=True)
        cache.mkdir(parents=True)
        (tools / ".dart_tool").mkdir(parents=True)
        (root / "packages/sample").mkdir(parents=True)
        (root / "bin/internal/engine.version").write_text("a" * 40)
        (cache / "engine.stamp").write_text("a" * 40)
        (cache / "engine_stamp.stamp").write_text("a" * 40)
        (tools / "pubspec.yaml").write_text("name: flutter_tools\n")
        (tools / "pubspec.lock").write_text("packages: []\n")
        (tools / ".dart_tool/package_config.json").write_text(json.dumps({
            "configVersion": 2, "packages": [
                {"name": "flutter_tools", "rootUri": "../"},
                {"name": "sample", "rootUri": "../../sample/"},
            ],
        }))

    revision = "a" * 40
    engine = tmp_path / "engine_stamp.json"
    engine.write_text(json.dumps({
        "git_revision": revision, "build_time_ms": 1, "content_hash": "hash",
        "git_revision_date": "2026-06-09T00:00:00Z",
    }))
    monkeypatch.setattr(repack, "_extract_archive", extract)
    monkeypatch.setattr(repack, "_apply_required_patches", lambda *_args: None)
    monkeypatch.setattr(repack, "_apply_overlay", lambda *_args: None)
    monkeypatch.setattr(repack, "_synthetic_git", lambda *_args, **_kwargs: "b" * 40)

    class FakePackage:
        def gen_resource(self, _name):
            return iter(())

        def debuild(self, candidate, *, section):
            pytest.fail("packaging must not begin after kernel compile failure")

    monkeypatch.setattr(repack, "_package_object", lambda *_args: FakePackage())
    monkeypatch.setattr(repack, "_compile_and_load", lambda *_args: (_ for _ in ()).throw(
        repack.RepackError("compiler rejected staged input")))

    with pytest.raises(repack.RepackError, match="compiler rejected"):
        repack.repack(source, output, engine_stamp_json=engine, repo_root=repo)
    assert not output.exists()


def test_provenance_includes_compiled_snapshot_and_reused_engine(tmp_path):
    path = tmp_path / "provenance.json"
    data = {
        "source_archive_sha256": repack.SOURCE_SHA256,
        "flutter_version": "3.47.2",
        "package_version": "3.47.2-1",
        "engine_revision": "a" * 40,
        "engine_reused": True,
        "snapshot_sha256": hashlib.sha256(b"kernel").hexdigest(),
        "native_elf_inventory": {"dart": hashlib.sha256(b"ELF").hexdigest()},
    }

    repack.write_provenance(path, data)

    assert json.loads(path.read_text()) == data


def _make_fake_runtime(tmp_path, *, compile_mode="ok", load_mode="ok"):
    root = tmp_path / "flutter"
    dart = root / "bin/cache/dart-sdk/bin/dart"
    launcher = root / "bin/flutter"
    dart.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    dart.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in --version) echo 'Dart SDK version: 3.13.2' >&2; exit 0;; esac\n"
        f"if [ '{compile_mode}' = fail ]; then exit 9; fi\n"
        "for arg in \"$@\"; do case \"$arg\" in --snapshot=*) out=${arg#--snapshot=};; esac; done\n"
        f"if [ '{compile_mode}' = empty ]; then : > \"$out\"; else printf '\\220\\253\\315\\357kernel' > \"$out\"; fi\n"
    )
    launcher.write_text(
        "#!/bin/sh\n"
        f"if [ '{load_mode}' = fail ]; then echo 'load rejected' >&2; exit 8; fi\n"
        "echo 'Flutter 3.47.2'\n"
    )
    dart.chmod(0o755)
    launcher.chmod(0o755)
    config = root / "packages/flutter_tools/.dart_tool/package_config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}")
    entry = root / "packages/flutter_tools/bin/flutter_tools.dart"
    entry.parent.mkdir(parents=True)
    entry.write_text("void main() {}\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "bin/flutter"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
    return root


def test_native_compiler_output_is_loaded_by_flutter_launcher_without_rebuild(tmp_path):
    root = _make_fake_runtime(tmp_path)

    version, size = repack._compile_and_load(root, "", tmp_path / "private")

    assert "Dart SDK version" in version
    assert size > 8
    assert (root / "bin/cache/flutter_tools.stamp").read_text().strip() == (
        subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                        text=True, capture_output=True).stdout.strip() + ":"
    )


@pytest.mark.parametrize(("compile_mode", "load_mode", "message"), [
    ("fail", "ok", "command failed"),
    ("empty", "ok", "kernel snapshot"),
    ("ok", "fail", "command failed"),
])
def test_compile_or_launcher_load_failure_fails_closed(tmp_path, compile_mode, load_mode, message):
    root = _make_fake_runtime(tmp_path, compile_mode=compile_mode, load_mode=load_mode)

    with pytest.raises(repack.RepackError, match=message):
        repack._compile_and_load(root, "", tmp_path / "private")
