# Termux ARM64 Android gen_snapshot

Code and local commits are ready; real engine-build, new-deb and device acceptance remain blocked by missing prerequisites. The pre-existing package is not proof of this candidate.

## Objective and scope
Build Android ARM64 AOT generators that execute on ARM64 Termux. Reject incompatible executables before copying, cache reuse and packaging. Keep Flutter 3.47.2, debug and unrelated installation behavior unchanged.

## Delivery and rollback
- Branch: `fix/github-actions-termux-flutter`; user-selected feature-branch-chain, no push or PR.
- Initial boundary: `b479322cd8905159a9bae20430d7fd172f7a5d4b`.
- Slice 1: `fba107b014aa0b6a0bfa26c6cf4dfec56401c880` — ELF/deb checks, independent CI and tests; 200 additions, 0 deletions. Exact isolated staged-tree tests: 13 passed.
- Slice 2: `54c649c3addaf06d8104c1516f4a0eb4bce2f4bf` — host routing, validated outputs/caches and tests; 292 additions, 64 deletions. Depends on slice 1.
- Source/test total: 556 authored changed lines; each slice below the approximately 400-line heuristic. Passive verification record is a separate commit.
- Rollback: revert slice 2 before slice 1; these changes do not include unrelated versions/install behavior.

## Tasks
- [ ] T1: Implement and prove ARM64 Termux host generators for release/profile.
  Route: delegated direct (multi-file new logic and preparation trigger). Code and tests committed in slices 1/2; real acceptance remains open.
  Scope: `build.py`, `patches/3.47.2/engine.patch`, `.github/workflows/build-deb.yml`, `tests/test_gen_snapshot.py`, `tests/test_build_pipeline.py`, `tests/test_build_freshness.py`, `tests/test_challenger_m3_gen3_stress.py` (related cache fixtures only).
  Acceptance still pending: actual pinned GN/Ninja output and Android ARM64 AOT codegen, new release/profile executables and deb, release APK/device execution.
- [x] T2: Record exact verification and real build/device blockers.
  Route: inline recovery-document bookkeeping plus delegated independent verification. Evidence below and behavior commit identities above.
  Documentation commit locator: `git log -1 --format=%H -- odd/tasks/termux-arm64-gen-snapshot.md`.

## Cause and correction
Android target CPU does not select the host executable. Previously `termux_cross_host` had no consumer; x64 fallback output was copied to `clang_arm64`, and package checks omitted both generators.

The patch selects a separate Termux ARM64 host toolchain while retaining the Android target and Linux/debug fallback. Checkout-pinned GN with engine cwd resolves the qualified generator output. Build/cache/deb checks require executable ELF64 AArch64 with `/system/bin/linker64`; CI independently extracts and inspects release/profile packaged binaries.

## Verification
- Final focused: `.venv-task/bin/python -m pytest -q tests/test_gen_snapshot.py tests/test_build_pipeline.py tests/test_build_freshness.py tests/test_challenger_m3_gen3_stress.py` — **65 passed in 4.50 seconds**.
- Complete suite: `timeout -s INT -k 15 600 .venv-task/bin/python -m pytest -p no:cacheprovider tests/ -q` — **620 passed, 3 failed, 5 skipped in 557.30 seconds**, exit 1; no tests deselected.
- All remaining failures reproduce on baseline: `test_challenger_m1_stress.py::test_preflight_check_failures`; `test_challenger_m2_gen3_stress.py::test_adversarial_flutter_project_config_strict_pipefail_and_modes[--mode=B-/Android/Sdk/build-tools/35.0.0/aapt2]`; `test_challenger_m2_stress.py::test_stress_broken_aapt2_symlink_handling`.
- Cache-fixture and GN executable/cwd corrections observed RED/GREEN. Initial ELF implementation RED blocked by missing dependencies, not claimed.
- Python compilation, bash syntax, repository sanity, version drift, patch syntax/hunk counts and whitespace passed. Actual patch application unavailable without pinned checkout. `shellcheck`/`actionlint` unavailable.
- Automatic review not completed: slice 1 assessed high; returned untracked-selection continuation omitted committed-only selectors. No review transaction started or unsafe continuation executed. User explicitly authorized these candidates without automatic review; global/clone mode unchanged, no approval claimed.

## Real build and artifact evidence
- Flutter checkout, `flutter/engine/src` and pinned GN absent. NDK/toolchain resolve to `None`; sysroot lacks `usr/include`/`usr/lib`.
- Both release/profile configure probes: `ValueError: Android NDK path is not set or toolchain path is invalid (ndk='None', toolchain='None')`.
- Both build probes: `FileNotFoundError` for missing engine source. No cloning/sync/download, real compilation or new deb generation performed.
- Existing `flutter-termux-3.47.2-arm64/flutter_3.47.2_aarch64.deb`, 201,289,524 bytes: both generators ARM aarch64 with `/system/bin/linker64`; complete new validator passed. This archive predates the candidate.
- Installed release/profile generators: x86-64 with `/lib64/ld-linux-x86-64.so.2`, different hashes/sizes from archive. Their actual provenance/replacement remains unproven.

## Next step
Use a prepared Linux host for pinned release/profile GN/Ninja builds, verify both new outputs with `file`, generate/validate a new deb and test `flutter build apk --release --target-platform android-arm64` on device. Do not complete T1 until observed. Keep archive/install provenance and three baseline test failures as separate follow-ups.
