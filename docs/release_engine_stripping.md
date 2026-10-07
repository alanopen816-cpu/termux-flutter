# Termux release engine stripping

Termux release APKs can use the ARM64 `libflutter.so` supplied by Flutter's Maven
repository. That file is not built or normally shipped by this SDK package, so the
package validator requires the strip helper but does not require an Android engine
library to exist in the `.deb`.

## Scope

On Linux Termux only, the installed Flutter Gradle plugin adds a final action to the
matching Android Gradle Plugin release strip task. It may strip exactly the staged
`arm64-v8a/libflutter.so` under AGP's `intermediates/stripped_native_libs/release`
output. Flavored release variants are accepted when AGP keeps them in that `release`
artifact partition. Custom build types whose name is not exactly `release` are not
hooked. Debug, profile, `libapp.so`, generators, other ABIs, Maven caches, and shared
SDK files are not targets. The APK packaging task consumes the same AGP output after
the strip task completes.

The hook is enabled only when Gradle reports Linux and `TERMUX_VERSION` is present.
The helper independently checks the Linux kernel identity and Termux environment,
then validates `$PREFIX/bin/llvm-strip` as an executable AArch64 ELF. A missing,
wrong-architecture, or unusable native tool fails the release build rather than
silently packaging the large library.

## Safety checks

The helper accepts only a regular ELF64 little-endian AArch64 shared object from the
release ARM64 staged path. It writes a unique same-directory candidate, runs native
`llvm-strip --strip-unneeded`, and validates the candidate before atomic replacement.
It preserves program headers and loadable bytes except the ELF section-table metadata
fields updated by stripping, plus allocated sections, dynamic symbol and string tables,
dependencies, SONAME, and relocation sections. The measured pair differs in six header
bytes for those section-table fields; executable content is unchanged. The staged output must
remove all nonempty `.debug_*`, `.symtab`, and `.strtab` sections, save at least 1 MiB,
and finish below 64 MiB. An already-stripped valid file below that bound is left
untouched. Failures before publication leave the staged original unchanged.

Gradle fingerprints the helper, native strip tool, and Python executable as inputs to
the AGP task. Thus changes to the helper/tool invalidate the prior result; the output
fingerprint represents the post-strip staged library that the package task consumes.

## Verification boundary

The repository's package validation requires the helper to be present and validates a
bundled release library only at the unambiguous SDK path
`opt/flutter/bin/cache/artifacts/engine/android-arm64-release/libflutter.so`. It checks
ELF64/AArch64 shared-object validity, rejects nonempty debug/static symbol sections,
and enforces the 64 MiB bound. Debug/profile engine cache paths and project APK paths
do not receive the release rule because their build mode is not inferable from those
package paths. Archive validation alone is not proof that a newly rebuilt APK is
smaller. The isolated R4 runtime checkpoint below now proves patched Gradle plugin
compilation and release APK reduction; R4 profile rebuild, device checks, and arbitrary
plugin compatibility remain separate.

## Archive validator mode boundary

Only `opt/flutter/bin/cache/artifacts/engine/android-arm64-release/libflutter.so` is
unambiguously a bundled Android ARM64 release engine library. The validator parses that
file even when it is below the size limit, so wrong-ISA/invalid ELF and residual debug
or static symbols fail closed. Libraries under debug/profile paths and APK-style
`lib/arm64-v8a/` paths are not classified as release by filename alone and are not
subjected to this guard.

## R4 package checkpoint — 2026-10-07

The current release helper is included in the freshly repacked
`artifacts/install-ready-r4/flutter_3.47.2_aarch64.deb` (Debian version
`3.47.2-4`, 214,412,776 bytes, SHA256
`e6801a4ae87bacb439c7e3afb7402020d703d637dc8b1bd31e72c5b2b2a34a6a`). The
repacker and independent `build.validate_deb_artifacts` validation succeeded.
The extracted helper and `post_install.sh` match current source hashes, and the
extracted `postinst` is executable mode 0755. Provenance records a newly
compiled FlutterTools kernel using Dart 3.13.2, while explicitly identifying
the engine/native ELF payload as reused from the pinned CI archive, not a fresh
GN build. All 29 native ELF paths and hashes match that source archive,
including the Android ARM64 release/profile generators. Flutter version
execution from an isolated R4 extraction reports Flutter 3.47.2 / Dart 3.13.2.

The actual extracted R4 package was exercised in a private install copy. Packaged
postinst configure ran twice (19.56s / 17.06s) with stable private configuration;
version reported Flutter 3.47.2 and Dart 3.13.2 without rebuilding the tool. A new
project completed create, pub get, and analyze without `useMyApt` or manual edits.
Release APK built in 284.80s, debug APK in 156.76s, and final release rebuild in
61.98s. The final release APK is 17,633,782 bytes (16.816885 MiB), SHA256
`b3fd3954abde0ba201873e20d08611212c5f5db92056a8b8b81887401237751c`, at
`/data/data/com.termux/files/usr/tmp/i4-r4-runtime-1xuon1y5/flutter_size_test/build/app/outputs/flutter-apk/app-release.apk`
for `com.example.flutter_size_test`.

| Measure | Before strip | R4 release | Change |
|---|---:|---:|---:|
| Release APK | 171,050,446 B (163.126417 MiB) | 17,633,782 B (16.816885 MiB) | −153,416,664 B |
| `lib/arm64-v8a/libflutter.so` (stored/uncompressed) | 165,164,528 B (157.513168 MiB) | 11,747,864 B (11.203636 MiB) | −153,416,664 B |
| DWARF sections | 146,197,936 B | 0 B | removed |
| `.symtab` + `.strtab` | 7,218,036 B | 0 B | removed |
| Dynamic symbols / defined exports | 22,499 B / 70 | 22,499 B / 70 | preserved |

Top ten ZIP members before/after (uncompressed bytes; stored/compressed bytes):

| ZIP member | Before | R4 release |
|---|---:|---:|
| `lib/arm64-v8a/libflutter.so` | 165,164,528 / 165,164,528 | 11,747,864 / 11,747,864 |
| `lib/arm64-v8a/libapp.so` | 5,373,464 / 5,373,464 | 5,373,456 / 5,373,456 |
| `classes.dex` | 519,924 / 237,984 | 519,924 / 237,985 |
| `assets/flutter_assets/packages/cupertino_icons/assets/CupertinoIcons.ttf` | 257,628 / 115,944 | 257,628 / 115,944 |
| `assets/flutter_assets/NOTICES.Z` | 98,088 / 96,239 | 98,088 / 96,239 |
| `kotlin/kotlin.kotlin_builtins` | 30,920 / 5,503 | 30,920 / 5,503 |
| `assets/flutter_assets/shaders/ink_sparkle.frag` | 21,856 / 5,275 | 21,856 / 5,275 |
| `assets/flutter_assets/shaders/stretch_effect.frag` | 17,616 / 3,503 | 17,616 / 3,503 |
| `kotlin/collections/collections.kotlin_builtins` | 8,625 / 1,890 | 8,625 / 1,890 |
| `resources.arsc` | 6,920 / 6,920 | 6,920 / 6,920 |

The 8-byte `libapp.so` difference and 1-byte `classes.dex` stored-size difference
are between distinct project outputs, not attributed to stripping. The engine retained
70 exports, 8 NEEDED entries, JNI_OnLoad, SONAME, build ID, and program-header/LOAD
contract. Six changed bytes were ELF section-table bookkeeping outside loadable data.
The original debug Maven library hash and both packaged release/profile generators were
unchanged. Reused caches and all 1,056 protected live paths had no deltas. Report:
`/data/data/com.termux/files/usr/tmp/i4-r4-runtime-1xuon1y5/report.json`.
The independent `verify_result.py` rerun passed.

This was a private extracted-copy runtime build, not replacement of the installed SDK.
R4 profile rebuild, APK install/launch on a device, and arbitrary third-party plugin
compatibility remain unproven. The full suite result (787 passed, 1 supplied baseline
Termux preflight failure, 5 skipped) predates only the archive-validator correction;
the focused 58-test suite and R4 archive validator passed after that correction. The
prior R3 repeat-harness mtime assertion was a false negative; its addendum confirmed
identical APK bytes/hash and staged metadata across repeats.
