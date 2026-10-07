import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
POST_INSTALL = ROOT / "scripts/install/post_install.sh"


def _patch_program():
    source = POST_INSTALL.read_text(encoding="utf-8")
    match = re.search(
        r"patch_release_engine_strip\(\) \{\n    python3 - \"\$1\" <<'PY'\n(.*?)\nPY\n\}",
        source,
        re.DOTALL,
    )
    assert match, "post-install must register its release-engine source patch"
    return match.group(1)


def test_installer_gradle_patch_is_idempotent_and_release_only(tmp_path):
    plugin = tmp_path / "FlutterPlugin.kt"
    plugin.write_text(
        "package com.flutter.gradle\n"
        "import org.gradle.api.tasks.Copy\n"
        "class FlutterPlugin {\n"
        "            val compileTaskName = flutterCompileTaskName(variant.name)\n"
        "}\n",
        encoding="utf-8",
    )
    program = _patch_program()
    subprocess.run(["python3", "-c", program, str(plugin)], check=True)
    once = plugin.read_text(encoding="utf-8")
    subprocess.run(["python3", "-c", program, str(plugin)], check=True)
    assert plugin.read_text(encoding="utf-8") == once
    assert once.count("strip${FlutterPluginUtils.capitalize(variant.name)}DebugSymbols") == 1
    assert 'variant.buildType == "release"' in once
    assert "OperatingSystem.current().isLinux" in once
    assert '!System.getenv("TERMUX_VERSION").isNullOrEmpty()' in once
    assert "PathSensitivity.NONE" in once
    assert "doLast {" in once
    assert "val stripTask = this" in once
    assert 'it.parentFile.name == "arm64-v8a"' in once
    assert '"--variant", "release"' in once
    assert '"--llvm-strip", stripTool.absolutePath' in once


def test_release_hook_requires_agp_stripped_native_library_output():
    source = POST_INSTALL.read_text(encoding="utf-8")
    assert '"stripped_native_libs" + File.separator + "release"' in source
    assert 'throw GradleException("Expected exactly one staged release ARM64 libflutter.so' in source
    assert 'inputs.file(stripHelper).withPropertyName("termuxStripHelper")' in source
    assert 'inputs.file(stripTool).withPropertyName("termuxLlvmStrip")' in source
