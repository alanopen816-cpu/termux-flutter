# FlutterTools cache publication

`post_install.sh` compiles and publishes `flutter_tools.snapshot` together with the
launcher key in `flutter_tools.stamp`. It must not erase a working snapshot before
the replacement has compiled and loaded successfully.

## Layout and publish sequence

The cache uses immutable generations under
`bin/cache/.flutter_tools_generations/`, one pointer at
`bin/cache/.flutter_tools_current`, and stable public links:

```text
flutter_tools.snapshot -> .flutter_tools_current/flutter_tools.snapshot
flutter_tools.stamp    -> .flutter_tools_current/flutter_tools.stamp
```

The finalizer first requires `pubspec.lock` to be at least as fresh as
`pubspec.yaml`; package resolution belongs outside finalization, and a stale lock
fails before any cache-path change. It compiles a kernel snapshot into a
same-filesystem staging directory. It copies Flutter package sources into an
isolated temporary SDK root with symlinks dereferenced, copies valid external
`file:` package roots into scratch `.packages/` and remaps the package config,
then loads the staged kernel there with private `HOME`, `PUB_CACHE`, and cache
directories. It also validates and copies the installed
`bin/cache/engine_stamp.json`, which FlutterTools consumes when running its
informational `--version` command; missing or malformed metadata fails before
publication rather than allowing a network fallback from the isolated check.
The matching `engine.stamp` and `engine_stamp.stamp` cache markers are copied and
validated against the JSON revision so `cache.updateAll(informative)` treats this
artifact as current in the validation root.
The validation invocation uses `--no-version-check --suppress-analytics` to
avoid unrelated update checks and telemetry. Non-file URIs and missing roots are rejected. It requires a
successful compiler and isolated load, nonempty snapshot, and exact staged stamp
before changing either public path.

Each published generation also carries `flutter_tools.inputs.sha256`, a versioned
fingerprint over FlutterTools source, package-config bytes and resolved package
roots (including external `file:` roots), compiler/runtime executable bytes, and
tool arguments. A prior pair is reusable only when both the normal launcher stamp
and this manifest match. Legacy regular-file pairs and older generations without
a trusted manifest compile once before establishing provenance. The stamp itself
remains exactly `HEAD:FLUTTER_TOOL_ARGS`, so upstream launcher behavior is
unchanged. Inputs are fingerprinted again after validation and immediately before
the pointer switch; observed source/compiler changes abort publication.

When the existing public paths are regular files (including after a successful
upstream rebuild), the finalizer first copies their visible contents and metadata
into an immutable baseline generation. It points `.flutter_tools_current` at that
baseline, then replaces each public path with a link through the pointer. Both
paths therefore retain their prior visible bytes, mode, owner, and mtime during
conversion. Only after the new generation is complete and both public links are
wired does one atomic pointer rename publish it. A valid pair with the exact
checkout revision and `FLUTTER_TOOL_ARGS` key, a fresh `pubspec.lock`, and a
matching input manifest is reused. Legacy regular-file or manifest-less pairs
must compile successfully before conversion.

The finalizer takes an exclusive bounded Python `fcntl.flock` on the installed
`bin/internal/shared.sh` inode, matching the launcher's FD 7 lock without
requiring an external `flock` executable. This serializes finalizers and launcher
rebuilds that take the slow path. The launcher checks validity before taking that
lock, so this is not a promise that arbitrary concurrent readers cannot span a
pointer change.

## Failure and limits

- Compile, staged-stamp, input-manifest, isolated-load, metadata-copy, or pre-publication rename
  failure leaves the visible old pair unchanged. Temporary generations may remain
  orphaned; active and baseline generations are intentionally not garbage-collected.
- If SIGKILL interrupts initial wiring when no old snapshot/stamp exists, the
  finalizer recognizes only its exact stable-link targets through a marked baseline
  generation with the corresponding absent member, then safely retries conversion.
  Other broken or unknown symlinks fail closed.
- SIGINT/SIGTERM before pointer publication aborts staging without changing the
  old pair. If SIGKILL occurs before the pointer rename, the pointer still selects
  the old generation; after it, it selects the complete new generation.
- Files and containing directories are fsynced where supported. Filesystem,
  device, and power-loss behavior is not universally guaranteed by POSIX rename
  alone.
- Upstream `shared.sh` can later move the public snapshot link to `.old` and write
  regular files directly during its own rebuild. After a successful rebuild, a
  later finalizer safely re-baselines that regular pair. This mechanism does not
  make an interrupted upstream rebuild transactional.
- Two separate public-path lookups can straddle the pointer rename. Readers that
  need a coherent pair across concurrent publication must pin one generation.
- This is a finalizer transaction only. It does not make earlier post-install
  operations or the complete package installation atomic.
