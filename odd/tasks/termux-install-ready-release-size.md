# Termux install readiness and release APK size

Audit and copy-only measurements are complete. The freshly repacked `3.47.2-4`
deb passed archive validation and private extracted-copy configure, project, and
debug/release build acceptance. Installed-SDK replacement, R4 profile rebuild,
device APK installation/opening, and arbitrary plugin compatibility remain open.

## Latest checkpoint — 2026-10-07

- R4 archive: `artifacts/install-ready-r4/flutter_3.47.2_aarch64.deb`, package
  `3.47.2-4`, 214,412,776 bytes, SHA256
  `e6801a4ae87bacb439c7e3afb7402020d703d637dc8b1bd31e72c5b2b2a34a6a`.
  It was generated from the pinned verified CI archive (SHA256
  `f3236fdc40cf85712aacea5ccb8d55611f76d6380c12f19276566f27db22188c`) with
  current sources at `48a1131d8416f41e161068113a6fd1cdf2c24123`; Flutter remains
  3.47.2. The provenance explicitly says engine/native ELF payload is reused,
  not a fresh GN build. FlutterTools kernel was freshly compiled with Dart
  3.13.2 (57,214,760 bytes, SHA256
  `6e6c13605ca6f66648d6ce554ade0ae8cf4d371a82a824e7bb567f97616de43a`).
- Both repacker archive validations and an independent
  `build.validate_deb_artifacts` pass succeeded. Extracted `postinst` is mode
  0755 and byte-matches `scripts/install/postinst.sh`. Packaged
  `share/flutter/strip_release_engine.py` and `share/flutter/post_install.sh`
  match their current source SHA256 values. All 29 source/candidate native ELF
  paths and hashes match; Android ARM64 release/profile `gen_snapshot` are
  unchanged and recorded in the R4 report. Extracted candidate's Flutter CLI
  reports Flutter 3.47.2 and Dart 3.13.2.
- Current source SHA256: helper `scripts/install/strip_release_engine.py`
  `f22ba81912cd74f01decf1c46adc61b00c931e4f4683725375789ff54d6f7e96`;
  hook/package patch source `scripts/install/post_install.sh`
  `f15c1306186375d1e21043fc1d0dd77fe4b87701d900e04f23b012914e1f9722`;
  repacker `scripts/package/repack_verified_payload.py`
  `aa725230326011a2be5c8ec050ee785d2115780e144e893ce5b8e1983f16fdb3`.
- Historical R3 private-copy release/debug/profile measurements are superseded
  for release/debug by the actual R4 private-copy report below. R4 did not run a
  profile build, install the APK on a device, or replace the real installed SDK.
- R4 runtime report `/data/data/com.termux/files/usr/tmp/i4-r4-runtime-1xuon1y5/report.json`:
  extracted packaged postinst configure twice passed (19.56s / 17.06s) with
  stable private configuration; Flutter 3.47.2 / Dart 3.13.2 reported without
  tool rebuild. New create/pub/analyze passed without `useMyApt` or manual
  edits. Release build 284.80s, debug build 156.76s, final release rebuild
  61.98s; final release APK is 17,633,782 B (16.816885 MiB), SHA256
  `b3fd3954abde0ba201873e20d08611212c5f5db92056a8b8b81887401237751c`.
  APK path: `/data/data/com.termux/files/usr/tmp/i4-r4-runtime-1xuon1y5/flutter_size_test/build/app/outputs/flutter-apk/app-release.apk`
  (`com.example.flutter_size_test`); independent `verify_result.py` rerun passed.
  `libflutter.so` is 11,747,864 B (11.203636 MiB), with zero DWARF/static
  symbols, 70 exports, 8 NEEDED entries and JNI/SONAME/build ID/program-header
  contract preserved. Original debug Maven library hash, both generators,
  reused caches, and all 1,056 protected live paths were unchanged. The report
  contains the exact before/after APK and top-10 ZIP-member table; those bytes
  are also summarized in `docs/release_engine_stripping.md`. Six changed bytes
  are ELF section-table bookkeeping outside loadable data; 8-byte `libapp.so`
  and 1-byte stored `classes.dex` differences are between distinct projects,
  not attributed to stripping.
- Full suite: 787 passed, 1 failed, 5 skipped in 1,247.60s. The failure is the
  supplied baseline Termux preflight mismatch
  `tests/test_challenger_m1_stress.py::test_preflight_check_failures`; report:
  `/data/data/com.termux/files/usr/tmp/i4-full-verification-ae9t7vnz/report.json`.
  Lightweight py_compile, bash syntax, `check_repo.py`, version-drift and
  `git diff --check` checks passed; 1,056 protected live paths had no deltas.
- Validator correction after that full-suite run: only the exact
  `opt/flutter/bin/cache/artifacts/engine/android-arm64-release/libflutter.so`
  path is now classified as the bundled release library. It is parsed as ELF
 64-bit little-endian AArch64 even below the size limit and rejected for an
  invalid/wrong-ISA ELF, residual debug/static symbols, or size above 64 MiB.
  Debug/profile engine paths and APK-style `lib/arm64-v8a/` paths are not
  classified as release. Regression tests first reproduced four failures and
  one pass, then passed 5/5; the requested focused modules passed 58/58. The
  R4 archive validator passed again; archive bytes and SHA are unchanged because
  this correction changes validation policy only. The full suite has not been
  rerun after this validator-only correction.
- Engram task mirror is pending: this worker had no authoritative registered
  session ID and performed no Engram writes.

## Objective and constraints

Make `pkg install ./flutter_3.47.2_aarch64.deb` configure a usable SDK automatically,
then make new Android ARM64 projects build without manual post-install/useMyApt.
Remove proven release symbol bloat without changing executable engine payloads blindly.
Keep Flutter 3.47.2, ARM64 Dart, release/profile generators, debug/hot reload and Linux.
Preserve user configuration and `~/android-sdk` data. No push or PR.
Existing installed SDK/APKs stay unchanged during experiments; use isolated copies.

## Delivery

- Branch: `fix/github-actions-termux-flutter`; initial boundary `bf2440804840e3d5f5597892c6467aef4039a4f0`.
- Strategy: existing feature-branch-chain, small logical work-unit commits.
- Forecast: approximately 1,000–1,500 authored changed lines across the units below;
  the 400-line guideline does not justify omitted tests or compressed code.
- Current implementation commits: `764ee3de457bf650a20a9fc40fbd8f939e62ff14` (I1),
  `adf54b2fe397e68efa80c78cc1549e14c2e2f536` (I2a).
  Additional commits: `10ec6e950c086a1a06d47e168937df7c57bb31ba` (I2b),
  `aaebdd1ab81ae289ee0b9ae9e7dd7d3fb0a38252` (I3a implementation).
  Running authored changes: 4,821; prior pending probe work is preserved.
  I2a is one cohesive transaction, fault-matrix and compatibility unit; the planning
  guideline does not justify omitting its failure tests. No PR slices have been published.
- Rollback each behavior with its tests; do not revert the validated ARM64 generator work.

## Evidence

- Actual local release/profile generators are AArch64 with `/system/bin/linker64`.
  User reports installed debug/release APKs run on Android 16; clean-new-deb proof is separate.
- Actual `~/flutter_termux_test` release APK: 171,605,902 bytes. Its uncompressed/stored
  `libflutter.so`: 165,164,528 bytes; `libapp.so`: 5,373,464 bytes.
- Release library SHA256 `62021ffab1836b3caef4707b6b07e345c65f6ed13443093c24039c21e25ead68`
  matches Maven release JAR and merged/stripped intermediates. DWARF: 146,197,936 bytes;
  symbol/string tables: 7,218,036 bytes. Copy-only strip: 11,747,864 bytes, exit 0.
  Dynamic exports/dependencies/LOAD layout preserved; six ELF section-metadata header bytes changed.
  Original checksums unchanged; reduced APK/runtime acceptance not yet performed.
- AGP 9.1 bytecode copies originals when strip is absent or fails. Selected SDK has no
  configured NDK 28.2.13676358; old HOME SDK NDK tools are x64. Historical choice is unproven.
- Installer already replaces strip tools, but only under the selected SDK and hides copy errors.
- Canonical platform-tools has native links but no package metadata; platform-tools-2 has
  registered metadata and x64 tools. Exact historical creation event is not retained.
- The original CI package has no postinst; committed packaging now supplies checked postinst. Installer always recompiles FlutterTools and has
  critical suppressed errors/network/HOME side effects requiring bounded corrections.
- Kernel named flutter_tools.snapshot executes directly in native Dart/DartVM scratch.
  Actual FlutterTools compiles offline to kernel in 17.40s (65,854,672 bytes); direct native
  version execution passed twice in an isolated copy. Shell-launcher integration,
  cross-host compatibility, performance and reproducible Git/stamp identity remain unproven.

## Tasks

- [x] I1: Preserve packaged ARM64 artifacts and make cache migration atomic/idempotent.
  Route: delegated direct; multi-file behavior and runnable regression tests.
  Scope: existing pending alias cleanup plus `scripts/install/post_install.sh`,
  `tests/test_android_snapshot_cache.py`; related old review warnings are new follow-up work.
  Checks: observed RED/GREEN, failed migration leaves original unchanged, repeat install,
  release/profile alias separation and legitimate download preservation.
  Progress: committed as `764ee3de457bf650a20a9fc40fbd8f939e62ff14`. New atomicity/mode tests observed
  five RED failures; both valid legacy postimages also observed RED before compatibility fixes.
  Final focused three-file suite: 53 passed in 309.38s with explicit installed DARTVM;
  syntax/whitespace and parent syntax spot-check passed. Independent exact installed-source
  copy replays both exit 0, preserving SHA256 and mode0600; live SDK unchanged.
  Candidate: two files, 386 additions/55 deletions (441 authored lines), including prior alias
  cleanup and compatibility tests. This cohesive protection unit slightly exceeds the planning
  heuristic; no tests are omitted or minified. Native high-risk review granted, four lenses
  returned no findings; lineage `review-96b0e876de80c106` acknowledged and authority burned.
  Commit tree exactly matches reviewed tree `ac30160118f59cfe4a2d593c34407d84470ce90e`.
  Whole-installer locking/concurrency and clean package reinstall are not proven by I1; I2 covers them.
- [x] I2a: Make FlutterTools finalization transactional (completed scoped block).
  Route: delegated direct; multi-file logic, fault-injection tests and isolated runtime proof.
  Scope: finalizer, its tests and transactional guarantee documentation only; no postinst,
  aapt2/SDK configuration, stripping or CI changes in this unit.
  Design: staged generation containing snapshot/stamp, metadata-preserving conversion of
  old regular paths, one atomic generation-pointer publication. Bounded Python flock
  coordinates finalizers and the actual shared.sh startup lock without external flock.
  Limits: no guarantee for arbitrary readers spanning pointer changes; upstream launcher
  rebuilds remain outside this finalizer transaction. Validate compatibility on an isolated copy.
  Checks: observed RED/GREEN for success, compiler/stamp/empty-invalid/rename failures,
  prior artifacts or absence, repeated runs and signals; old SHA/modes unchanged on failure;
  bash -n, diff check, focused suite and actual copied-SDK version before/after failure/success.
  Progress: committed as `adf54b2fe397e68efa80c78cc1549e14c2e2f536`; five files,
  1,170 additions/106 deletions. Original finalizer regression tests observed RED;
  source-provenance regression also observed RED before the input manifest fix.
  Kernel generation and isolated load precede publication; input fingerprints prevent
  stale reuse after installer source patches while preserving exact HEAD:args stamps.
  Validation requires consistent engine JSON and cache markers so the copied runtime
  does not fetch informative artifacts. Final focused suite: 86 passed in 175.59s.
  Independent transaction suite: 33 passed in 12.61s; bash syntax/diff checks passed,
  including parent spot check. Final source SHA256:
  b38e3870e07c84e69d4d274e9e4c6e1e46e75d175a40cb114d14744a49dc0999.
  Actual copied launcher before/after injected compiler failure both exit 0; old pair
  SHA, mode0600, owner, mtime and inode unchanged. Native compile/load/publication
  exit 0 in 60.42s, copied launcher accepts it without rebuilding. Three repeats
  preserve pair, manifest, pointer and metadata; same-key source comment regenerates
  once (57.00s), subsequent reuse is stable. Original SDK and 1,047 Dart files unchanged;
  copied release/profile generators and five protected cache markers unchanged.
  Report: `$TMPDIR/i2a-final-offline-h2ikhh7o/report.json`.
  Native assessment: high; native review unavailable because exact provider untracked
  continuation loses committed-only/base selectors and targets an unrelated document.
  No I2a START, consent, freeze or approval was executed; independent verification
  above is functional proof, not a fabricated native receipt. No mode toggles.
  Complete installer, fresh deb and device APK acceptance remain pending.
- [x] I2b: Isolate every pytest process from persistent user Flutter configuration.
  Route: delegated direct; multiple subprocess helpers and a runnable leakage regression.
  Evidence: named snapshot-cache test inherits real HOME via offline helper; installer
  writes fixture Android SDK to ~/.flutter_settings, which overrides Android env.
  Scope: pytest HOME/XDG/Gradle/Pub cache isolation and subprocess helpers/tests only.
  Checks: observed RED in a private caller HOME, GREEN with caller sentinel unchanged;
  focused and full suite preserve the actual installed settings hashes/metadata.
  Progress: committed `10ec6e950c086a1a06d47e168937df7c57bb31ba`;
  184 additions/19 deletions, six files. Caller sentinel test observed RED safely.
  Focused 74 passed in 251.02s; independent selected 17 passed in 36.62s and actual
  user settings SHA/mode/ownership/mtime/inode unchanged. Verifier caught optional
  DARTVM assertion failing Python-only CI; corrected with conditional discovery and
  deterministic fake executable preservation test. No-VM selected 18 passed in 40.75s;
  final isolation 3 passed in 3.85s, parent spot check 3 passed in 3.72s.
  Full suite rerun remains pending. Tests intentionally do not repair already polluted
  live settings; postinst must set canonical SDK safely. Native unit assessment medium,
  under budget; pending range since last acknowledged boundary remains high. Exact
  native collection continuation again changed committed base-diff to unrelated live
  tracking doc; no START or approval executed for that wrong target.

- [x] I2c: Isolate installed SDK roots and deny inherited Flutter/Dart wrappers in tests.
  Route: delegated direct; unsafe real smoke-test path and multiple fixture corrections.
  Commits: `a04abae`, `4af879d`. Selected writer24 passed51.92s;
  independent24 passed52.21s and adversarial2 passed0.30s. Syntax/diff checks pass.
  All1,054 postincident SDK/HOME entries unchanged; earlier installed SDK incident is
  not restored or hidden. Fresh guards precede inherited PATH; mocks are explicit.
  Native review unavailable as documented; no fabricated approval.
- [ ] I3b: Correct the real Cache architecture API comparison and Termux Gradle wrapper injection.
  Route: delegated direct; two confirmed production blockers with regressions.
  Scope: library installer patches, repack patch registration/revision, dedicated tests/docs.
  Checks: actual Cache API returns arm64, release/profile choose linux-arm64 independently,
  legitimate non-Termux downloads preserved; newly injected Gradlew uses native Bash;
  existing custom wrappers and non-Termux behavior preserved; repeat patches idempotent.
  Prove native private Cache selection and fresh project wrapper before expensive compression.
  Implementation committed `cafda0d4e08159e49d7dd090901f06125bd819e0` (357 additions/28 deletions).
  Writer48 passed77.93s; independent48 passed47.98s. Actual fullcopied FlutterTools
  native Dart/DartVM compile/run43.53s/43.85s exit0, architecturearm64; directselection
  empty; includeAllPlatforms excludes Linux-x64 while Darwin/Windows remain.
  Native wrapper execution/custom/nonTermux/idempotence/fault-preservation pass.
  Independent proof caught abstractFile constructor; fixed with actual package:file
  unique samefilesystemtemporary directory beforecommit. All1,054 postincident paths
  preserved. Bash/Python/diff checks pass. RDD unavailable (known selectorloss), notapproved.
  Fullsuite, actual r2kernel/deb and APK smoke remain pending, so acceptance is unchecked.
  Fullsuite:770 passed/1 knownTermuxpreflightfailed/5 skipped831.67s;1054baseline preserved.
  R2actual archive214,433,360 bytes SHA6e34159bd65bf145ec6234287f0e6d57ffb35f17687db45020eaff3f97d1832f,
  validator0/147.41s,29nativehashes preserved/postinst0755. Genuinepostinsttwice/version/create/pub/analyze pass.
  Actualdebug1/1.57s ENOENT: create_base directlycopieswrapperbypassinghook; existingwrapper skipscopy.
  Cachefixworks/no6x64downloads/genspreserved. Reopen runtimewrapperacceptance; minimumexistingdefault
  getExecutable normalization pluscorrectfreshcreatecache, regression anddistinct3.47.2-3 artifact.
  R2 preserved and NOTAPK-ready; no realinstall/release/deviceproof yet.
  Runtimewrappercorrection committed `48a1131d8416f41e161068113a6fd1cdf2c24123`
  (164add/29del): sharednormalizer inactualcreate_basecopy and existinggetExecutable.
  Independent50passed105.42s, actualfullsourceDart/DartVM30.09/30.74sexit0;
  genuinecreate_baseinjection+GradleUtils29.86sexit0, fresh/existingdefaultnativeBash,
  custom/nonTermux/failureoriginalpreservation/idempotencepass. All1054baseline preserved.
  Writerinterruptedafterunresponsivework; no inventedwriterresult. R3artifactplannedseparate,
  Debian3.47.2-3. Needfreshkernel/fullsuite/actualpackageanddebugreleaseacceptance.

- [ ] I3a: Automatically select native Termux AAPT2 for new Gradle projects.
  Route: delegated direct; managed global properties, runtime guard and safe user updates.
  Scope: installer plus dedicated managed configuration helper/data and focused tests.
  Checks: preserve unrelated properties/custom settings; repeat installer safely;
  runtime Termux guard; actual new-project debug/release use native AAPT2.
  Progress: implementation committed `aaebdd1ab81ae289ee0b9ae9e7dd7d3fb0a38252`;
  six files, 561 additions/18 deletions including full Java-properties preservation tests.
  Helper checks native Termux and AArch64 Android linker, updates canonical Flutter SDK
  JSON and managed Gradle setting atomically, preserves valid custom properties/metadata.
  Three independent blockers observed RED then fixed: whitespace/escaped Java properties,
  denied ownership changes and cross-unit fixture isolation. Final focused105 passed
  in306.54s; independent33 passed5.83s, noVM1 passed0.17s; realGradle9.3.1 private
  user-home/global-vs-project precedence proof passed12.92s. Live settings unchanged.
  Remains unchecked until the extracted new-deb new-project debug/release builds pass.
- [ ] I2: Add actual executable postinst and safe local SDK bootstrap.
  Route: delegated direct; package lifecycle and installer logic across files.
  Scope: `package.py`, `package.yaml`, installer/maintainer scripts and packaging tests.
  Checks: real control archive, configure/reconfigure, critical failures visible, snapshot/stamp
  reuse and synthetic Git identity; no unsafe user HOME overwrite or x64 downloads.
  Progress: first bootstrap/control unit committed `0ad97deec0e291193b3000f42e6e36c494cb26dc`.
  Writer123 passed191.15s; independent88 passed11.92s. Actual native copied postinst
  configure0 in12.78s, repeat0 in6.39s, copied launcher0 in1.23s without rebuild.
  DevTools metadata is validated/copied into loader isolation; failure leaves settings intact.
  12,143 original SDK/HOME files unchanged. Real new-deb install/APK proof still pending.
  Unit: ten files, 1,103 additions/27 deletions; one cohesive package control/bootstrap
  and mandatory validation unit with tests. Native assessment unassessable/high due
  untracked declaration; known exact collection selector loss remains unavailable.
  No native approval claimed. The current
  finalizer risk is addressed by I2a; earlier full-install side effects remain to be made safe
  before automatic invocation. Implement a separate checked configure-only bootstrap,
  never recursively apt/pkg or clean ELF payloads. Native AAPT/aapt2 are separate installed
  package owners. Missing external Android SDK must be an explicit prerequisite, not a
  false Android-ready success. Do not redistribute unverified proprietary SDK archives.
  Real native copied-launcher kernel compatibility is proven
  in I2a; cross-host package provenance and complete package installation remain unproven.
- [ ] I3: Configure Termux Android SDK, global aapt2 and registered native platform-tools.
  Route: delegated direct; environment/configuration boundaries and fixture tests.
  Scope: package profile/wrappers, installer, useMyApt fallback and Android configuration tests.
  Checks: both SDK variables canonical, user settings preserved, new projects build without helper,
  dependencies verified and duplicate active platform-tools detected without deleting user data.
- [ ] I4: Supply native stripping to the NDK used by Gradle; prove smaller rebuilt release APK.
  Route: delegated direct; native tool resolution plus isolated build verification.
  Checks: actual native strip executable, no false success, reduced library sections,
  APK/library compressed and uncompressed bytes and top 20 ZIP entries; debug/profile preserved.
  Progress: current release hook/helper and repack inclusion are implemented. R4
  archive `artifacts/install-ready-r4/flutter_3.47.2_aarch64.deb` passed mandatory
  validation and independent extraction checks; package SHA, size, source and
  kernel provenance are recorded in the latest checkpoint above. Extracted R4
  helper/post-install hashes match current sources, `postinst` is executable,
  and all 29 native ELF paths/hashes match the verified CI source archive.
  R4 private-copy acceptance now proves packaged configure-twice, no-rebuild
  version, create/pub/analyze without manual project setup, AGP plugin compile,
  actual release stripping, debug preservation, and identical repeated release
  APK bytes. See latest checkpoint and `docs/release_engine_stripping.md` for
  exact measurements/top-10 table. R4 profile build, APK device install/open,
  arbitrary plugin compatibility and installed-SDK replacement remain
  unproven, so I4 stays unchecked. Prior R3 repeat-mtime false negative is
  historical; its addendum confirmed same bytes/hash and staged metadata.
- [ ] I5: Enforce mandatory deb artifacts and exercise install lifecycle in CI.
  Route: delegated direct; validators, workflows and deterministic packaging smoke tests.
  Scope: `build.py`, packaging tests, `.github/workflows/ci.yml`, `.github/workflows/build-deb.yml`.
  Checks: ARM64 Android Dart/dartvm/dartaotruntime and both generators, tool sources/pubspec,
  actual snapshot/stamp, declared aapt/aapt2 dependencies, scripts and executable postinst.
- [ ] I6: Generate/validate new deb and perform clean Termux debug/release smoke acceptance.
  Route: delegated direct; packaging, full suite and real isolated installation commands.
  Checks: provenance documented (no reused binary presented as fresh GN output), clean installation,
  version/doctor/create/pub/analyze/debug/release and release APK installation/opening.

## Verification and next step

Use deterministic pytest RED/GREEN for each behavior. Existing working-tree baseline:
631 passed, 3 pre-existing failures, 5 skipped; the three failure names are recorded in
`odd/tasks/termux-arm64-gen-snapshot.md`. Do not claim tests equal device acceptance.
Native review remains user-owned; each eligible new candidate needs its own consent.
Current priority: I2b pytest isolation, I3a automatic native AAPT2, I2 safe postinst,
then a real validated deb and clean install smoke. I4 stripping is explicitly deferred.
Repack existing verified ARM64 payload only with honest provenance if cross-build inputs
remain unavailable; never present the old CI engine as newly compiled. Preserve the mandatory archive snapshot/stamp requirement while
investigating kernel integration; do not replace it silently with post-install-only validation.
Production engine checkout/cross-build outputs are absent; no new engine build or deb is claimed.
Verified repack premise: old CI deb SHA f3236fdc40cf85712aacea5ccb8d55611f76d6380c12f19276566f27db22188c,
102 contained relative package roots, direct required source patches, native payload Dart
kernel compile71.68s (57,194,944 bytes), actual launcher0 before/after whole-tree move,
no tool rebuild. 1,040 archive runtime files and1,054 watched live files unchanged.
Premise report: `$TMPDIR/i3-repack-premise-crjredgl/report.json`.
Production utility committed `d34af200bcf30902041036dfd6217268eb4c6b39`;
resource-routing correction `d26f54fca42261d6ce1fcf4bfb7e0d9f4610d56c`.
Native compiler/launcher proof passed; first actual compressed candidate rejected
because generic Package exclusions pruned engine cache. Explicit cache mapping fixed
and actual preflight29/29 ELF hashes plus1,038 Dart files preserved; focused78 passed
3.95s. Second actual final-generation run is pending, no validated final deb claimed.
Current authored utility unit1,083 lines pluscorrection109 lines; tests/docs staywithbehavior.
Native review remains unavailable; latest parent untracked-selection submission was
invalid_request, no authority/mutation started. No false native approval claimed.
Measurement evidence is under `$TMPDIR/flutter-size-measure-vrvsfkth` and
`$TMPDIR/dart-kernel-premise-59r50f30`.

## Latest acceptance checkpoint (2026-10-07)

- Published first candidate: `artifacts/install-ready/flutter_3.47.2_aarch64.deb`,
  214,392,948 bytes, SHA256
  `2471a4b5fbe9364c4d48db61bc2ccda08152a7a263c18d52a04a5d331fab5ec0`.
  Production source `f5eaf20479a62d871c7c833c125fb8007bc7f74c`, Debian3.47.2-1.
  Reused CI engine, freshly compiled native kernel; mandatory validation, 29 native ELF
  hashes and executable0755 postinst passed. This is NOT the final APK-ready candidate.
- Private extracted postinst configure14.58s/repeat6.94s/version1.22s all exit0.
  Config metadata stable; createoffline/puboffline/analyze pass. Real installation/doctor
  and APK/device acceptance pending. Canonical external complete Android SDK remains a prerequisite.
- Debug blocked at generated Gradle wrapper shebang `#!/usr/bin/env bash` (ENOENT).
  Gradle wrapper injection needs Termux-only native shebang normalization.
- Real Cache probe shows getHostPlatformArchName returns `arm64`, not CLI `linux-arm64`.
  Existing guard compares wrong API value; six official x64 siblings were downloaded,
  although protected ARM64 release/profile hashes remained unchanged. Minimum correction
  compares architecture `arm64` without changing Cache API or blocking legitimate downloads.
- Initial fullsuite760 passed/3 failed/5 skipped1159.51s exposed installed SDK preservation
  incident: old device-evidence unit invoked real smoke installer inheriting PREFIX.
  1,040 mode-only changes to0777 and snapshot replacement observed; HOME settings unchanged.
  No blind restore. Test-only evidence harness/SDK-root isolation committed `a04abae`.
  Original unsafe test path was confirmed, but per-PID attribution of every mutation is unavailable.
- Later guarded fullsuite757 passed/9 failed/5 skipped613.76s is INCONCLUSIVE because
  ad-hoc command guards and fixture SDK/controlled PREFIX mismatches invalidated it.
  Fixture compatibility corrections observed RED then81 passed501.93s.
- Nativeusr/bin hardcoded Flutter/Dart wrappers escape SDK-directory filtering.
  Private default guards now block them, but inherited PATH position cannot confer
  fixture trust. Sole writer fixes sanitization to put guards before all inherited paths;
  fixtures must prepend explicit mocks after sanitization. Safe actual resolver probes
  never execute caller wrappers; all1,054 postincident baseline files remain unchanged.
- Next: close test guard/fixture unit, fix production Cache API comparison and generated
  wrapper shebang with tests, generate a new distinct candidate without overwriting this
  artifact, rerun clean fullsuite/private native bootstrap/new-project debug/release,
  then require real installation/device acceptance. No push, PR or stripping.

## Current verified R3 checkpoint

Production commit: `48a1131d8416f41e161068113a6fd1cdf2c24123`.
Artifact: `artifacts/install-ready-r3/flutter_3.47.2_aarch64.deb` (214,467,108 bytes,
Debian 3.47.2-3), SHA256 `672da3eabc23a083facab200d05dfdcec26fd270b78de6e529a6a0f2c9d252fe`.
Generation exit0/1688.50s; independent validator exit0/180.97s. All29 reused CI ELF
hashes match; five protected binaries AArch64/system linker; executable0755 postinst.
Fresh kernel57287272bytes. No fresh GN build claimed; no bundled Android SDK.

Focused50passed105.42s; fullsuite772passed/1knownTermuxpreflightfailed/5skipped1340.70s.
Genuine private extracted postinst twice/version/create/pub/analyze PASS. Freshproject
normal debug PASS157.46s, release PASS183.80s, using managed global native AAPT2.
No useMyApt, manual project fixes, tool rebuild or six Linux-x64 updates. Both generators
and private protected metadata unchanged; all1054 live postincident baseline paths
unchanged. This does not restore or conceal the earlier installed SDK incident.

Private fixture problems (conflicting deprecated Android preference env, incomplete
SDK35-only copy and forced-offline missing AARs) were corrected only in scratch;
canonical verified native SDK36 and legitimate anonymous Maven downloads suffice.
Release APK171050446bytes, libflutter165164528bytes/libapp5373448bytes stored uncompressed;
stripping remains explicitly deferred. Actual pkg installation, doctor/device and APK
installation/opening acceptance remain unchecked. Do not claim complete.

Evidence: native runtime `$TMPDIR/r3-private-runtime-b26hnjk3/report.json`, independent
archive `$TMPDIR/r3-independent-acceptance-pu0oct47/report.json`, fullsuite
`$TMPDIR/full-suite-independent-run-48a1131d/`. Local install overwrites current SDK:
obtain explicit approval and backup SDK/config before doing so; no actual install yet.
The bounded documentation worker was interrupted after no edits/progress; no new docs
acceptance update or docs commit is claimed. Native RDD remains unavailable/unapproved.

## Authorized real installation phase

User explicitly authorized current Termux installation of exact R3 package after
SHA256672da3eabc23a083facab200d05dfdcec26fd270b78de6e529a6a0f2c9d252fe
and full SDK/config backups. Keep backup through all tests. Route delegated direct:
packaging lifecycle and actual execution; no source/push/PR/stripping changes.
- [x] R1: Verify archive/check active writers/disk and derive all package/postinst touched paths.
- [x] R2: Retain verified complete old Flutter SDK and relevant Flutter/Gradle/profile/Android
  plus overwritten owned payload/config and package metadata backups; originalmetadata unchanged.
- [ ] R3: Install exact absolute R3 .deb via normal current package manager; failure stops,
  no destructive automated repair/force-dependency or forced-overwrite bypass.
- [ ] R4: Ordered version/doctor/Dart/gens/AAPT2/create/analyze/debug/release without useMyApt.
- [ ] R5: Only after both builds pass, install release APK on the current local Android device
  and verify launch; no unknown remote sessions/devices/credentials. Interactive Android
  installer may require user confirmation; never infer installation from starting an intent.
Do not delete backup. If installation/postinst fails, preserve exact failure/state and stop.

### Real installation result — stopped on failure

Preflight exactSHA/dependencies/liveness/simulation passed. Backup retained:
`backups/real-r3-install-fxih75gn/original-state.tar`1257082880B,
SHA55cd46121be1c482f10e83c6b459d0268c03a5b4b006dd223c33549da0275277,
dir0700/file0600,tarcreate/list/compareexit0;27598entries and21absents recorded.
Escalated actual apt-get --no-download install exactR3deb exited1 beforeunpack/configure:
`fdsan: attempted to close file descriptor 5, expected to be unowned, actually owned by FILE* 0x7cfee170c0`.
No automaticpostinst success observed; dpkg stillflutter3.47.2 installokinstalled.
AlloriginalSDK/userconfiguration unchanged. Onlyapt extended_states inode/mtime changed,
with identical contentSHA. Deb andbackupSHAverifiedagain. Version/doctor/architecture/
newproject/debugrelease/deviceAPK checks SKIPPED by userfailurestopgate.
NOretry/forcedinstall/rollback/postinst rerun/destructiverepair. Keepbackup; awaitexplicit
newuserinstructionbeforeanyfurtherinstallationtroubleshooting ornewattempt.
Evidence `backups/real-r3-install-fxih75gn/phase2.json` and`install-failure.log`.

## Current release-size work — I4

User reports successful later manual dpkg3.47.2-3 install/postinst/version/Androiddoctor/
ARM64gens/nativeAAPT2/create/analyze/debugrelease/adbinstall. Parentdpkg-query confirms
3.47.2-3installok; earlierAPTfailure retained ashistory, notcurrentinstallationstate.
Scope now ONLY releaseengine stripping; no cleanup/version/architecture/push/PR.

Read-only measured current `~/flutter_final_test` APK171050446bytes and actual Maven
ARM64releaseJAR enginea804. OriginalSO165164528B SHA62021ffab1836b3caef4707b6b07e345c65f6ed13443093c24039c21e25ead68.
DWARF146197936B/static7218036B; nativecopystrip11747864B SHA
 d70bbedc2b157630f90123a32ca894a6fa1816ec10cda9f335bada0692b376ee.
All70dynamicexports/8NEEDED/SONAME/buildID/relocations/LOADlayout preserved; allocated
sectiontotal11810041B unchanged, onlysix ELFsectionbookkeepingloadedbytes differ.
No original changes;1056currentprotectedentries preserved.
Androidlibflutter.so sourcedMavenFlutterPlugin, NOT currentdeb engine; localGNoutputsabsent.
AGP skipped stripping due missing native NDKstriptool; merged/stripped hashesidentical.

- [x] S1: Map actual source/pipeline and measure required ELF reports/copy-only stripping.
  Route delegated readonly; evidence `$TMPDIR/i4-release-copy-strip-p7ecm30y/report.json`.
- [x] S2: Native release-only staged-strip helper and Flutter Gradlehook beforeAPK packaging.
  Route delegated direct: multiplebehaviorfiles/integration/faulttests. Shared MavenSDK
  source/debug/profile/libapp/gens unchanged. Failclosed ELF/dynamic/LOAD/metadata validation,
  unique samefilesystemstaging andatomicreplace. R4 private build compiled and executed the
  actual AGP hook; release then debug then release-repeat produced byte-identical final
  release APKs. R4 profile rebuild remains pending. Implementation/proof is partial until
  parent records the authorized work-unit commit; no new GN engine claimed.
  Work-unit commit: `34d9dbeb8e7a0c26a1faf42fa19d0c64695cba99`; implementation and
  release/debug evidence are complete, while profile/device acceptance stays in S4/S5.
- [x] S3: Packaginghelper/hook and appropriate release-engine validation/regression tests.
  Engine NOTshipped AndroidSO: doNOT require nonexistent AndroidengineinDeb. Validate
  releaseengine only at `opt/flutter/bin/cache/artifacts/engine/android-arm64-release/libflutter.so`;
  enforce valid ELF64/AArch64, no debug/static symbols, and a generous 64 MiB maximum. Debug,
  profile and APK-style paths are not classified as release. Focused 58-test suite and
  mandatory R4 archive validator passed; implementation/proof remains partial until parent
  records the authorized work-unit commit.
  Work-unit commit: `34d9dbeb8e7a0c26a1faf42fa19d0c64695cba99`; independent final
  focused verification: 58 passed in 22.69s; exact R4 archive revalidation: exit 0,
  154.89s. Report: `$TMPDIR/i4-validator-independent-wjbmkzur/report.json`.
- [ ] S4: Focused/full tests, independentverification, privatefreshrelease/debug/profile
  preservation plusnewdebhelper/hook validation, honestreusedCIengineprovenance.
  Focused suite after validator correction: 58 passed; full suite 787 passed/1 supplied
  baseline Termux preflight failure/5 skipped is from before that validator-only correction.
  R4 private release/debug builds and independent report passed; R4 profile/device work is
  pending.
- [ ] S5: ReducedAPK install/open on authorizedcurrentAndroiddevice; no arbitraryADB
  destination/credentials. Genuine debug+releaseafterstrip; sizebytes/MiB/top10 comparison.
KeepfunctionalinstalledSDK and existingfullbackup intact; copiesfirst. Currentnative
copystrip measurement is NOT runtime/plugin/deviceproof. Artifactlanguage English.
Strategy existing feature-branch-chain logicalworkunitcommits; forecast~800–1400authoredlines,
400heuristicnotcap/codegolf. Candidate review switch unchanged; neverclaim nativeapproval.

### Work-unit closure and next decision

Implementation commit `34d9dbeb8e7a0c26a1faf42fa19d0c64695cba99` contains 990 authored
changed lines across ten scoped code/test/doc files. This is one cohesive release
strip transaction, Gradle integration, package routing and regression unit; the
planning heuristic did not justify dropping fault or mode-preservation tests.
No artifact, backup, unrelated probe, or old gen_snapshot tracking changes were committed.
Native assessment: high, review due (`high_risk`), base `48a1131d8416f41e161068113a6fd1cdf2c24123`.
The exact assessment-issued preflight returns `intended_untracked_selection_required`,
but its submission tokens omit `--base-ref` and `--committed-only`. No unsafe submission,
START, candidate consent, freeze, or approval was executed; native review is unavailable
for this continuation. Its forecast horizon is partial, step 1: collect intended untracked
selection. The review switch remains enabled and unchanged; independent proof is not
a native receipt.
S4/S5 and overall I4 remain open. Next: obtain the user's exact current Android ADB
destination and authorized credential/session before any device probe, then install
and launch the preserved reduced APK. No live SDK replacement is needed. Remaining
R4 profile rebuild and arbitrary-plugin coverage are separate disclosed limitations.
Engram mirror and session summary remain pending because the runtime supplied no
authoritative registered session identity; no agent-attributed memory writes were made.

### Historical I4 independent verification checkpoint (superseded by R4)

Writer focused71 passed7.55s; independent71 passed8.16s. Native copy helper passed2.14s,
11747864bytes SHA d70bbedc2b157630f90123a32ca894a6fa1816ec10cda9f335bada0692b376ee.
Genuine private Gradle9.3.1 assembleRelease dry-run FAILED40.12s at compileKotlin:
actual variant.buildType is String?, not BuildType; injected doLast lambda uses an
invalid Kotlin DSL signature. No APK build attempted. Independent audit also requires
bounded lock/subprocess waits and checksum/metadata recheck immediately before publish.
This earlier R3 check is superseded for release/debug by the R4 private runtime report above.
S2/S3 implementation is now proven in R4 but remains uncommitted pending parent work-unit
closure; R4 profile and device checks remain open.
Evidence `$TMPDIR/i4-independent-native-ffx3_3wj/report.json`; all1056 live entries unchanged.
No new package, commit, installed SDK change or native approval claimed.
