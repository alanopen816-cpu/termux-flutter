# Termux package bootstrap requirements

The Debian `configure` maintainer action runs the bounded local bootstrap through
`post_install.sh --package-bootstrap`. It does not invoke the legacy full installer,
download Android components, run `pub get`, or repair package contents after unpacking.

Before changing persistent Android configuration, the bootstrap checks the packaged
ARM64 Android Dart runtimes and release/profile generators, FlutterTools sources and
resolved package metadata, engine cache metadata, and the kernel snapshot/stamp against
the package's minimal synthetic Git `HEAD`. It then loads the real snapshot in a private
temporary source/cache/HOME tree and applies the managed Android settings. The `.deb`
must therefore include the kernel snapshot, the matching minimal `.git` metadata, and
SDK-relative package roots; full Flutter Git history is not required or shipped.

An existing canonical SDK at `$PREFIX/opt/android-sdk` is a prerequisite. It must
contain Android platform 34, licenses, build-tools, and platform-tools/adb. Missing or
incomplete SDK contents stop configuration with an actionable error; this package does
not download, redistribute, or silently claim readiness for a proprietary SDK. The
installer updates `$HOME/.flutter_settings` without discarding unrelated keys and
manages the native Termux AAPT2 override in Gradle's global properties.

`build.py` normalizes contained FlutterTools `rootUri` values to relative paths, and the
deb validator rejects missing source/cache inputs, external roots, mismatched synthetic
Git/stamp identity, inconsistent engine markers, and absent/non-executable `postinst`.
This validates archive structure and provenance; it is not a clean-device install or
Android debug/release build acceptance result. A verified-payload repack and final
extracted-package launcher/device acceptance remain separate release checks.

## Verified local repack (not a fresh engine build)

When cross-build inputs are unavailable, the explicitly named repack utility can
rebuild the package around the pinned CI ARM64 payload. It accepts only
`flutter_3.47.2_aarch64.deb` with SHA-256
`f3236fdc40cf85712aacea5ccb8d55611f76d6380c12f19276566f27db22188c`; it never
downloads sources or changes the installed SDK. Example on native Termux:

```sh
python scripts/package/repack_verified_payload.py \
  --source "$HOME/termux-flutter/flutter-termux-3.47.2-arm64/flutter_3.47.2_aarch64.deb" \
  --engine-stamp-json "$PREFIX/opt/flutter/bin/cache/engine_stamp.json" \
  --output artifacts/install-ready-r3/flutter_3.47.2_aarch64.deb
```

The next candidate is Debian package revision `3.47.2-3`; the Flutter release remains
`3.47.2`. Earlier candidate artifacts are preserved, not overwritten. Revision `-2`
fixed the ARM64 cache selector and GradleUtils copy path, but real `flutter create`
uses a separate `create_base.dart` copy path, so a fresh project's default wrapper
still retained the unavailable `#!/usr/bin/env bash` interpreter. Revision `-3`
normalizes default wrappers on both creation and GradleUtils paths and repairs existing
default wrappers immediately before execution. Custom shebangs and non-Termux behavior
remain unchanged.

The matching engine metadata is read-only input and must name the exact engine
revision in archived `bin/internal/engine.version`; a missing or mismatched file is
an error, not a fallback. The pinned archive carries absolute CI paths for its cached
pub packages. The utility accepts them only as one consistent `flutter/.pub-cache`
source prefix anchored by the `flutter_tools` package entry, rebases them into the
extracted SDK, then strictly rejects any missing or escaping root. It runs the current mandatory source patches there, creates marked minimal
synthetic Git metadata for the launcher's `HEAD:FLUTTER_TOOL_ARGS` key, then uses the
archived native Dart compiler to build and load a new kernel snapshot. It uses a
private HOME/Pub cache/temp root and validates the candidate package plus every ELF
byte hash before publication. Before compilation, it probes Linux `renameat2` with
`RENAME_NOREPLACE` on the output filesystem; this keeps publication atomic without
depending on Python's optional `os.link` API or a racy exists-then-replace sequence.
If the kernel/filesystem lacks that primitive, the repack fails before expensive work.
The final copy preserves candidate bytes and mode, and an existing file, directory,
or symlink is never replaced. Current
package.yaml resources, wrappers, profile, postinst and configuration helpers are
applied. Since the general package walker intentionally skips directories named
`engine`, the repacker explicitly maps the archived `bin/cache/artifacts/engine`
runtime subtree to its matching prefix-relative SDK destination. Before compression,
the repacker verifies that every file and directory under every resolved package
root is routed, hashes the complete package-root file set into a compact provenance
manifest, and checks the required tool inputs, installer scripts, and native ELF
inventory. The final validator normalizes only the known Termux install prefix
(`data/data/com.termux/files/usr/`) to SDK-logical paths; it also accepts bare logical
paths in fixtures, rejects duplicate aliases and path escapes, and still requires the
mandatory files at their exact logical destinations. The extracted `.deb` is checked
independently as well. The provenance file records the input archive, workspace commit, reused
engine revision, Dart compiler, generated kernel hash, synthetic commit, and ELF
inventory.

This is deliberately **not** described as a fresh Flutter/engine GN build: engine
ELFs and Android generators remain byte-identical payloads from the pinned CI
archive. The minimal synthetic Git commit is a package compile key, not upstream
Flutter history. The Android SDK remains a separate pre-existing Termux prerequisite;
the repack does not bundle or install it. Repack validation is not a clean package
install or new-project debug/release acceptance.
