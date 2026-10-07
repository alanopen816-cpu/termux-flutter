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
