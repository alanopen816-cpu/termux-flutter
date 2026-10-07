import io
import tarfile
from pathlib import Path

import pytest

import package
from build import _ar_members


def test_deb_control_archive_contains_executable_postinst(tmp_path):
    root = tmp_path / "flutter"
    root.mkdir()
    postinst = tmp_path / "postinst.sh"
    postinst.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    postinst.chmod(0o755)
    control = {
        "Package": "flutter",
        "Version": "3.47.2-1",
        "Architecture": "aarch64",
        "Maintainer": "Termux Flutter <test@example.com>",
        "Depends": "bash, python, git, aapt, aapt2",
        "Description": "Flutter for Termux",
    }
    pkg = package.Package(
        root=root, arch="arm64", control=control, resource={}, tag="3.47.2",
        revision="1", maintainer_scripts={"postinst": postinst},
    )
    output = tmp_path / "flutter.deb"
    pkg.debuild(output)

    control_archive = next(data for name, data in _ar_members(output) if name.startswith("control.tar"))
    with tarfile.open(fileobj=io.BytesIO(control_archive), mode="r:*") as archive:
        script = archive.getmember("postinst")
        assert script.mode & 0o111
        assert archive.extractfile(script).read() == postinst.read_bytes()
        control_text = archive.extractfile(archive.getmember("control")).read().decode()
    assert "Version: 3.47.2-1\n" in control_text
    assert "Depends: bash, python, git, aapt, aapt2\n" in control_text


@pytest.mark.parametrize("script_name", ["preinst", "postrm", "prerm"])
def test_only_postinst_maintainer_script_is_supported(tmp_path, script_name):
    root = tmp_path / "flutter"
    root.mkdir()
    script = tmp_path / "script"
    script.write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises(ValueError, match="unsupported maintainer script"):
        package.Package(
            root=root, arch="arm64", control={
                "Package": "flutter", "Version": "3.47.2-1", "Architecture": "aarch64",
                "Maintainer": "test", "Description": "test",
            }, resource={}, maintainer_scripts={script_name: script},
        )


def test_symlinked_postinst_is_rejected(tmp_path):
    root = tmp_path / "flutter"
    root.mkdir()
    script = tmp_path / "actual-postinst"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    link = tmp_path / "postinst-link"
    link.symlink_to(script)
    pkg = package.Package(
        root=root, arch="arm64", control={
            "Package": "flutter", "Version": "3.47.2-1", "Architecture": "aarch64",
            "Maintainer": "test", "Description": "test",
        }, resource={}, maintainer_scripts={"postinst": link},
    )
    with pytest.raises(FileNotFoundError, match="unsafe maintainer script"):
        list(pkg.gen_control_files())
