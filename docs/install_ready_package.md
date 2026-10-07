# Install and validate the Termux package

Use the `.deb` with Termux's `dpkg`, then verify the installed Flutter toolchain
and build a fresh ARM64 Android project. The package does not contain an Android
SDK; a configured SDK must already exist at `$PREFIX/opt/android-sdk`.

## Install

Verify the SHA256 using the trusted checksum distributed with the exact package,
then install:

```sh
sha256sum flutter_3.47.2_aarch64.deb
dpkg -i /path/to/flutter_3.47.2_aarch64.deb
flutter --version
flutter doctor -v
```

The validated release artifact is Debian version `3.47.2-4`, Flutter `3.47.2`,
ARM64. Its repository copy is
`artifacts/install-ready-r4/flutter_3.47.2_aarch64.deb` (214,412,776 bytes,
SHA256 `e6801a4ae87bacb439c7e3afb7402020d703d637dc8b1bd31e72c5b2b2a34a6a`).
Use a release-published checksum when installing a published artifact; do not
assume this local artifact is a published release.

The package's post-install action verifies and configures packaged Flutter
resources. It does not download or bundle the Android SDK. Android platform 34,
licenses, build-tools and platform-tools are required. The real Termux setup
also detected SDK 36, while APK builds use API 34 for Termux AAPT2 compatibility.

## Fresh-project smoke test

```sh
flutter create my_app
cd my_app
flutter pub get
flutter analyze
flutter build apk --debug --target-platform android-arm64
flutter build apk --release --target-platform android-arm64
adb install -r build/app/outputs/flutter-apk/app-release.apk
adb shell monkey -p <application-id> 1
```

Replace `<application-id>` with the value in `android/app/build.gradle` or
`android/app/build.gradle.kts`. Confirm the app opens, renders Flutter UI, and
remains alive during a short foreground/background check. The tested reduced
release APK was 17,633,782 bytes (about 16.82 MiB); its `libflutter.so` was
11,747,864 bytes (about 11.20 MiB), with DWARF removed. Real-device runtime
success for this reduced APK was reported on Samsung SM-X716B / Android 16.

The recorded flow was: install `.deb`, `flutter --version`, `flutter doctor`,
create a project, analyze, debug build, release build, ADB install, launch, and
runtime check. Installation/build/device evidence must not be conflated: archive
validation alone does not prove a device launch, and device evidence applies to
the exact APK tested.

## Package validation for maintainers

Inspect package metadata and file modes before installation:

```sh
dpkg-deb -I artifacts/install-ready-r4/flutter_3.47.2_aarch64.deb
dpkg-deb -c artifacts/install-ready-r4/flutter_3.47.2_aarch64.deb
```

The automated validator checks the extracted package's required ARM64 Dart
runtimes, `gen_snapshot` release/profile generators, linker interpreter,
FlutterTools inputs, Android settings/configuration, release-strip helper, and
executable `postinst`. The R4 package reused verified CI engine payloads; it was
a verified-payload repack, not a new engine GN build. Its private package tests
covered configure, project creation/analyze, and release/debug/release builds.

## Environment notes and limits

- Prefer `dpkg -i /path/to/flutter_3.47.2_aarch64.deb`. A prior `pkg`/APT attempt
  stopped before unpacking with a Termux `fdsan` file-descriptor error. This was
  not a package installation failure; no APT workaround is claimed.
- The canonical SDK is `$PREFIX/opt/android-sdk`, with both `ANDROID_HOME` and
  `ANDROID_SDK_ROOT` expected to point there. Termux's native ADB is
  `$PREFIX/bin/adb`. A legacy `$HOME/android-sdk/platform-tools` entry left in a
  user's shell PATH can trigger Flutter's duplicate-ADB warning; do not delete
  the legacy SDK to silence it.
- Flutter's framework-age warning comes from the package's synthetic Git HEAD
  timestamp (`2000-01-01T00:00:00Z`), not a changed Flutter release version.
  The deterministic version/stamp metadata is left intact.
- Supported Android engine/host architecture is ARM64 (`arm64-v8a`). Linux
  desktop under Termux:X11 is experimental. iOS and x86-64 Linux host binaries
  are unsupported.
- The Android SDK is a separate prerequisite. At least 8 GB free storage is
  recommended; builds are memory-intensive and may need other workloads closed.
