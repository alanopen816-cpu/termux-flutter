<p align="center">
  <img src="assets/banner.png" alt="termux-flutter-wsl" width="800"/>
</p>

<h1 align="center">Flutter for Termux ARM64</h1>

<p align="center">
  <strong>A packaged Flutter SDK for Android/Termux ARM64 with APK builds, Linux desktop builds, and hot reload.</strong>
</p>

<p align="center">
  <code>flutter build apk</code> ✅ | <code>flutter build linux</code> ✅ | <code>flutter run</code> + Hot Reload ✅ | installable <code>.deb</code> ✅
</p>

<p align="center">
  <a href="README.md">中文</a> | <strong>English</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Flutter-3.47.2-02569B?logo=flutter" alt="Flutter Version"/>
  <img src="https://img.shields.io/badge/Dart-3.13.2-0175C2?logo=dart" alt="Dart Version"/>
  <img src="https://img.shields.io/badge/Target-aarch64-green" alt="Target"/>
  <a href="https://github.com/ImL1s/termux-flutter-wsl/actions/workflows/ci.yml"><img src="https://github.com/ImL1s/termux-flutter-wsl/actions/workflows/ci.yml/badge.svg" alt="CI"/></a>
  <img src="https://img.shields.io/badge/License-GPL--3.0-blue" alt="License"/>
</p>

<p align="center">
  <em>🍴 Forked from <a href="https://github.com/mumumusuc/termux-flutter">mumumusuc/termux-flutter</a></em>
</p>

<p align="center">
  <img src="assets/demo_hot_reload.jpg" alt="Flutter running on Termux with Hot Reload" width="600"/>
</p>

---

## Quick navigation

- [What this is](#what-this-is)
- [Current release and validation](#current-release-and-validation)
- [Quick install](#quick-install)
- [Create a project and build an APK](#create-a-project-and-build-an-apk)
- [Linux desktop / Termux:X11](#linux-desktop--termuxx11)
- [Build the `.deb` yourself](#build-the-deb-yourself)
- [CI/CD and release validation](#cicd-and-release-validation)
- [Documentation map](#documentation-map)
- [Limitations and notes](#limitations-and-notes)

## What this is

Flutter's ARM64 target support does not mean the official SDK can be used as an **Android/Termux host** SDK. Termux uses Android bionic, Android linker paths, and a different toolchain layout; the official Linux SDK assumes a glibc host.

This repository packages a Flutter SDK for Termux ARM64:

- Cross-compiles Flutter Engine, Dart runtime, and required host tools from WSL/Linux.
- Assembles an installable `flutter_3.47.2_aarch64.deb` package for Termux.
- Runs `post_install.sh` to patch Flutter Tools, the Gradle plugin, NDK/build-tools wrappers, Android SDK constraints, and Termux shebangs.
- Enables `flutter doctor`, `flutter create`, `flutter build apk`, `flutter build linux`, and hot reload through Termux:X11.

## Current release and validation

| Item | Value |
| --- | --- |
| Flutter | `3.47.2` |
| Dart | `3.13.2` |
| Architecture | `aarch64` / `arm64-v8a` |
| Release asset | [`flutter_3.47.2_aarch64.deb`](https://github.com/ImL1s/termux-flutter-wsl/releases/tag/v3.47.2-termux) |
| Debian package revision | `3.47.2-4` (Flutter remains `3.47.2`) |
| Validated package | `artifacts/install-ready-r4/flutter_3.47.2_aarch64.deb` |
| Package size | 214,412,776 bytes |
| Package SHA256 | `e6801a4ae87bacb439c7e3afb7402020d703d637dc8b1bd31e72c5b2b2a34a6a` |
| Release APK smoke size | 17,633,782 bytes (about 16.82 MiB) |
| Stripped `libflutter.so` | 11,747,864 bytes (about 11.20 MiB) |

### Device smoke test

Device runtime validation on Samsung SM-X716B / Android 16 / Termux was reported
for the reduced release APK. The report confirms installation, launch, rendered
Flutter UI, interaction, a live process, and background/foreground operation.
The `libdolphin.so` warning was non-fatal; no app crash, linker, JNI, or missing
Flutter symbol error was reported. Treat this as device evidence for the tested
APK, not a guarantee for every plugin or device.

| Check | Result |
| --- | --- |
| `dpkg -i` + `post_install.sh` | ✅ |
| `flutter --version` / `dart --version` / `dartvm --version` | ✅ |
| `flutter doctor -v` | ✅ expected channel/device warnings only |
| `flutter create` / `flutter analyze` | ✅ |
| Android ARM64 debug and release APK builds | ✅ |
| `adb install`, launch, UI and runtime | ✅ (reported for reduced release APK) |

## Quick install

### Requirements

| Item | Requirement |
| --- | --- |
| Device | Android ARM64 / aarch64 |
| Android | ARM64 Android device; Android 11+ recommended |
| App | Termux with `dpkg`; Termux:X11 only for Linux desktop GUI |
| Android SDK | Existing SDK at `$PREFIX/opt/android-sdk`, API 34 platform, licenses, build-tools and platform-tools |
| Storage | At least 8GB free space recommended; larger projects/build caches need more |
| Memory | Builds are resource intensive; close other workloads if Gradle/Dart is killed |

### Install the package

```bash
dpkg -i /path/to/flutter_3.47.2_aarch64.deb
flutter --version
flutter doctor -v
```

Verify the package SHA256 against its trusted release checksum before installation.
The tested installation path is `dpkg -i`; an earlier `pkg`/APT attempt failed
inside Termux package management before unpacking and is not evidence of a `.deb`
failure. Do not run `apt -f install` unless APT reports an actual dependency issue.

## Create a project and build an APK

Create and validate a new Android project using the package-managed Termux
configuration. APK builds target ARM64; use Android API 34 for compatibility
with the native Termux AAPT2 toolchain.

```bash
flutter create my_app
cd my_app
flutter pub get
flutter analyze
flutter build apk --debug --target-platform android-arm64
flutter build apk --release --target-platform android-arm64
adb install -r build/app/outputs/flutter-apk/app-release.apk
adb shell monkey -p <application-id> 1
```

Replace `<application-id>` with the Android application ID from the project.
The reduced release APK was reported to launch and render on a real Android
device. ADB should resolve to Termux's `$PREFIX/bin/adb`; if the legacy SDK's
ADB remains in a user's shell PATH, Flutter may still warn about duplicate
executables even though the native one is selected first.

## Linux desktop / Termux:X11

```bash
pkg install -y x11-repo termux-x11-nightly
termux-x11 :0 &
export DISPLAY=:0

flutter create --platforms=linux linux_demo
cd linux_demo
flutter run -d linux
```

Linux desktop builds are experimental and require Termux:X11; they are not part
of the Android ARM64 support guarantee. iOS is not supported. Linux x86-64 host
Flutter/Dart/engine binaries are incompatible with the Android/Termux ARM64 host.
The package does not bundle an Android SDK; use the existing canonical SDK at
`$PREFIX/opt/android-sdk`.

You can also build only:

```bash
flutter build linux --release
```

## Build the `.deb` yourself

A full build requires WSL/Linux, Android NDK, depot_tools, a Flutter Engine checkout, and usually takes multiple hours.

```bash
python3 build.py build_all --arch=arm64
```

Common step-by-step commands:

```bash
python3 build.py clone
python3 build.py sync
python3 build.py patch_engine && python3 build.py patch_dart && python3 build.py patch_skia
python3 build.py sysroot --arch=arm64
python3 build.py configure --arch=arm64 --mode=debug
python3 build.py build --arch=arm64 --mode=debug
python3 build.py build_dart --arch=arm64 --mode=debug
python3 build.py debuild --arch=arm64
```

See [`docs/guides/BUILD_GUIDE.md`](docs/guides/BUILD_GUIDE.md) for the full build process, mode matrix, and troubleshooting.

## CI/CD and release validation

| Workflow | Trigger | Runner | Purpose |
| --- | --- | --- | --- |
| `CI` | PR / push / manual | GitHub-hosted Ubuntu | Python, Shell, PowerShell, YAML, and package/docs contract sanity |
| `Build deb` | Manual | self-hosted Linux/WSL | Full Flutter Engine build, `.deb` packaging, optional release publishing |
| `Device smoke` | Manual | self-hosted Windows + ADB | Install the deb in Termux, then run doctor/create/APK/Linux smoke tests |
| `Release check` | Release / manual | GitHub-hosted Ubuntu | Verify release asset name, size, and SHA256 |

See [`docs/CI_CD.md`](docs/CI_CD.md) for runner requirements and local equivalents.

> Device smoke requires the tablet to stay awake and unlocked; secure lock screens block ADB text injection into Termux.

## Project layout

```text
termux-flutter-wsl/
├── .github/workflows/        # GitHub-hosted CI + self-hosted build/device gates
├── docs/                     # Long-form documentation, guides, and release notes
│   ├── README.md             # Documentation index
│   ├── CI_CD.md              # CI/CD, runner, and device-lab guide
│   ├── guides/               # Install, build, and upgrade guides
│   └── releases/             # Changelog and release notes
├── scripts/
│   ├── ci/                   # Lightweight repository contract checks
│   ├── device/               # ADB → Termux smoke automation
│   ├── install/              # Termux install and post-install patches
│   └── test/                 # Release / Termux E2E smoke scripts
├── patches/3.47.2/           # Flutter Engine / Dart / Skia patches
├── build.py                  # Main build CLI
├── build.toml                # Version, NDK, sysroot, and patch configuration
├── package.yaml              # .deb artifact mapping
└── install_flutter_complete.sh
```

## Documentation map

| Document | Purpose |
| --- | --- |
| [`docs/README.md`](docs/README.md) | Full documentation index |
| [`docs/guides/INSTALL_GUIDE.md`](docs/guides/INSTALL_GUIDE.md) | Install flow and Termux runtime prerequisites |
| [`docs/guides/BUILD_GUIDE.md`](docs/guides/BUILD_GUIDE.md) | WSL/Engine build, packaging, and troubleshooting |
| [`docs/guides/UPGRADE_GUIDE.md`](docs/guides/UPGRADE_GUIDE.md) | Checklist for upgrading to a new Flutter release |
| [`docs/CI_CD.md`](docs/CI_CD.md) | CI/CD, self-hosted runners, and device smoke |
| [`docs/releases/CHANGELOG.md`](docs/releases/CHANGELOG.md) | Version history |
| [`docs/releases/RELEASE_NOTES.md`](docs/releases/RELEASE_NOTES.md) | Current GitHub release body |

## Limitations and notes

- The supported target is **ARM64 / arm64-v8a**. 32-bit ARM and x64 Android gen_snapshot builds are out of scope.
- Android APK builds should use `compileSdk = 34` / `targetSdk = 34` to avoid Termux `aapt2` compatibility problems with newer `android.jar` files.
- `flutter doctor` can show a framework-age warning because the package uses deterministic synthetic Git metadata dated `2000-01-01`; changing this risks reproducibility and version/stamp behavior, so it is documented rather than falsified.
- A prior APT attempt stopped before unpacking with a Termux `fdsan` file-descriptor error. This is separate from the validated `dpkg -i` package installation; no APT workaround is claimed.
- Android SDK and ADB configuration is package-managed where possible. Existing user shell startup files can still append a legacy SDK path; the package does not delete that SDK or user data.
- `ninja flutter` does not produce a usable `dart` binary; source builds must run `build_dart()` separately.
- The release `.deb` is large, so install and first `post_install.sh` runs can take time.

## Troubleshooting entry points

- APK build / `aapt2` / compileSdk issues: [`docs/guides/INSTALL_GUIDE.md`](docs/guides/INSTALL_GUIDE.md)
- Engine / Dart / Skia patch or WSL build issues: [`docs/guides/BUILD_GUIDE.md`](docs/guides/BUILD_GUIDE.md)
- CI/CD, release assets, and device smoke: [`docs/CI_CD.md`](docs/CI_CD.md)
- Upgrading Flutter versions: [`docs/guides/UPGRADE_GUIDE.md`](docs/guides/UPGRADE_GUIDE.md)

## Acknowledgements

- Original project: [`mumumusuc/termux-flutter`](https://github.com/mumumusuc/termux-flutter)
- Termux community for the Android userspace, packages, and X11 ecosystem
- Flutter / Dart / Skia / Chromium Engine upstreams

## License

This project is licensed under [GPL-3.0](LICENSE).
