#!/bin/bash
# Flutter Termux Post-Install Script
# 安裝 deb 包後執行此腳本以完成 APK 構建環境配置

set -e

echo "=========================================="
echo "Flutter Termux Post-Install Configuration"
echo "=========================================="

# 路徑定義
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
FLUTTER_ROOT="${FLUTTER_ROOT:-$PREFIX/opt/flutter}"
ANDROID_SDK="${ANDROID_SDK:-$PREFIX/opt/android-sdk}"
DART_SDK="${DART_SDK:-$FLUTTER_ROOT/bin/cache/dart-sdk}"
if [ -z "${FLUTTER_PREBUILT_ENGINE_VERSION:-}" ] && [ -f "$FLUTTER_ROOT/bin/internal/engine.version" ]; then
    export FLUTTER_PREBUILT_ENGINE_VERSION="$(cat "$FLUTTER_ROOT/bin/internal/engine.version" 2>/dev/null | tr -d '\n\r')"
fi

# TMPDIR must be derived from PREFIX so any prefix stays self-contained.
# Hardcoding the Termux prefix here made runs with an overridden PREFIX (CI
# runners, custom prefixes) try to create /data and fail closed.
: "${TMPDIR:=$PREFIX/tmp}"
export TMPDIR
mkdir -p "$TMPDIR"
test -d "$TMPDIR" && test -w "$TMPDIR"

export PATH="$PREFIX/bin:$PATH"
PATCH_STATE_FILE="${PATCH_STATE_FILE:-$PREFIX/share/flutter/patch_state.json}"
BACKUP_DIR="${BACKUP_DIR:-$PREFIX/share/flutter/backups}"

MODE="${MODE:-apply}"
if [ "${1:-}" = "--check" ]; then MODE="check"; fi
if [ "${1:-}" = "--apply" ]; then MODE="apply"; fi
if [ "${1:-}" = "--status" ]; then MODE="status"; fi
if [ "${1:-}" = "--rollback" ]; then MODE="rollback"; fi
if [ "${1:-}" = "--lib" ]; then MODE="lib"; fi

if [ "$MODE" != "status" ] && [ "$MODE" != "check" ] && [ "$MODE" != "lib" ]; then
    mkdir -p "$BACKUP_DIR" "$(dirname "$PATCH_STATE_FILE")"
    if [ ! -f "$PATCH_STATE_FILE" ]; then
        echo "{}" > "$PATCH_STATE_FILE"
    fi
fi

declare -A STATE_TARGET
declare -A STATE_PREIMAGE
declare -A STATE_POSTIMAGE
declare -A STATE_STATUS
declare -A STATE_FUNCDIGEST
declare -a PATCH_ORDER
declare -A PATCH_FUNCS

# Parse existing state
regex_start='"([^"]+)"[[:space:]]*:[[:space:]]*\{'
regex_field='"([^"]+)"[[:space:]]*:[[:space:]]*"([^"]+)"'

if [ -f "$PATCH_STATE_FILE" ]; then
    while read -r line; do
        if [[ "$line" =~ $regex_start ]]; then
            P_NAME="${BASH_REMATCH[1]}"
        elif [[ "$line" =~ $regex_field ]]; then
            P_KEY="${BASH_REMATCH[1]}"
            P_VAL="${BASH_REMATCH[2]}"
            if [ "$P_KEY" = "target" ]; then STATE_TARGET["$P_NAME"]="$P_VAL"; fi
            if [ "$P_KEY" = "preimage" ]; then STATE_PREIMAGE["$P_NAME"]="$P_VAL"; fi
            if [ "$P_KEY" = "postimage" ]; then STATE_POSTIMAGE["$P_NAME"]="$P_VAL"; fi
            if [ "$P_KEY" = "status" ]; then STATE_STATUS["$P_NAME"]="$P_VAL"; fi
            if [ "$P_KEY" = "func_digest" ]; then STATE_FUNCDIGEST["$P_NAME"]="$P_VAL"; fi
        fi
    done < "$PATCH_STATE_FILE"
fi

save_state() {
    echo "{" > "$PATCH_STATE_FILE"
    local first=1
    for patch in "${PATCH_ORDER[@]}"; do
        if [ -n "${STATE_STATUS[$patch]}" ]; then
            if [ $first -eq 0 ]; then echo "  ," >> "$PATCH_STATE_FILE"; else first=0; fi
            echo "  \"$patch\": {" >> "$PATCH_STATE_FILE"
            echo "    \"target\": \"${STATE_TARGET[$patch]}\"," >> "$PATCH_STATE_FILE"
            echo "    \"preimage\": \"${STATE_PREIMAGE[$patch]}\"," >> "$PATCH_STATE_FILE"
            echo "    \"postimage\": \"${STATE_POSTIMAGE[$patch]}\"," >> "$PATCH_STATE_FILE"
            echo "    \"func_digest\": \"${STATE_FUNCDIGEST[$patch]}\"," >> "$PATCH_STATE_FILE"
            echo "    \"status\": \"${STATE_STATUS[$patch]}\"" >> "$PATCH_STATE_FILE"
            echo -n "  }" >> "$PATCH_STATE_FILE"
        fi
    done
    echo "" >> "$PATCH_STATE_FILE"
    echo "}" >> "$PATCH_STATE_FILE"
}

register_patch() {
    local name="$1"
    local target="$2"
    local func="$3"
    PATCH_ORDER+=("$name")
    STATE_TARGET["$name"]="$target"
    PATCH_FUNCS["$name"]="$func"
}

apply_patches() {
    local any_failed=0
    for patch_name in "${PATCH_ORDER[@]}"; do
        local target_file="${STATE_TARGET[$patch_name]}"
        local patch_func="${PATCH_FUNCS[$patch_name]}"

        if [ ! -f "$target_file" ]; then
            echo "  ⚠ $patch_name: target missing ($target_file)"
            continue
        fi

        local current_hash
        current_hash=$(sha256sum "$target_file" | awk '{print $1}')
        local state_status="${STATE_STATUS[$patch_name]}"
        local state_post="${STATE_POSTIMAGE[$patch_name]}"
        local func_digest
        func_digest=$(declare -f "$patch_func" 2>/dev/null | sha256sum | awk '{print $1}')

        if [ "$state_status" == "applied" ] && [ "$current_hash" == "$state_post" ]; then
            if [ "${STATE_FUNCDIGEST[$patch_name]}" == "$func_digest" ]; then
                if [ "$MODE" == "status" ] || [ "$MODE" == "check" ]; then echo "  ✓ $patch_name: already applied"; fi
                continue
            fi
            # The patch implementation changed since this file was written (a patch fix, e.g. the
            # forceNdkDownload() smart-cast fix). The stored postimage only proves the file matches
            # the *previous* implementation, so re-evaluate here: run the current patch on a scratch
            # copy and only patch for real when it would still change the file.
            local tmp_recheck
            tmp_recheck=$(mktemp "$TMPDIR/patch_recheck.XXXXXX")
            cp "$target_file" "$tmp_recheck"
            local recheck_changed=0
            if $patch_func "$tmp_recheck" 2>/dev/null; then
                if ! cmp -s "$target_file" "$tmp_recheck"; then recheck_changed=1; fi
            fi
            rm -f "$tmp_recheck"
            STATE_FUNCDIGEST["$patch_name"]="$func_digest"
            if [ $recheck_changed -eq 0 ]; then
                if [ "$MODE" == "status" ] || [ "$MODE" == "check" ]; then echo "  ✓ $patch_name: already correct"; fi
                continue
            fi
            if [ "$MODE" == "apply" ]; then echo "  ! $patch_name: patch updated, re-applying"; fi
        fi

        if [ "$MODE" == "status" ] || [ "$MODE" == "check" ]; then
            local tmp_check
            tmp_check=$(mktemp "$TMPDIR/patch_check.XXXXXX")
            cp "$target_file" "$tmp_check" 2>/dev/null || true
            if $patch_func "$tmp_check" 2>/dev/null && cmp -s "$target_file" "$tmp_check"; then
                echo "  ✓ $patch_name: already correct"
                rm -f "$tmp_check"
                continue
            fi
            rm -f "$tmp_check"
            echo "  + $patch_name: pending"
            continue
        fi

        if [ "$MODE" == "status" ] || [ "$MODE" == "check" ]; then
            continue
        fi

        echo "  Applying $patch_name..."
        if [ ! -f "$BACKUP_DIR/$patch_name.orig" ] || [ -z "${STATE_PREIMAGE[$patch_name]}" ] || [ "$current_hash" != "${STATE_PREIMAGE[$patch_name]}" ]; then
            cp "$target_file" "$BACKUP_DIR/$patch_name.orig"
        fi

        STATE_PREIMAGE["$patch_name"]="$current_hash"
        cp "$target_file" "$target_file.tmp"

        if ! $patch_func "$target_file.tmp"; then
            echo "  ✗ $patch_name: unknown upstream content (patch failed)"
            rm -f "$target_file.tmp"
            any_failed=1
            continue
        fi

        if cmp -s "$target_file" "$target_file.tmp"; then
            echo "  ✓ $patch_name: already correct"
            rm -f "$target_file.tmp"
            STATE_POSTIMAGE["$patch_name"]="$current_hash"
            STATE_FUNCDIGEST["$patch_name"]="$func_digest"
            STATE_STATUS["$patch_name"]="applied"
            continue
        fi

        mv "$target_file.tmp" "$target_file"
        local new_hash
        new_hash=$(sha256sum "$target_file" | awk '{print $1}')
        STATE_POSTIMAGE["$patch_name"]="$new_hash"
        STATE_FUNCDIGEST["$patch_name"]="$func_digest"
        STATE_STATUS["$patch_name"]="applied"
        echo "  ✓ $patch_name: successful"
    done

    if [ "$MODE" == "apply" ]; then
        save_state
    fi
    if [ $any_failed -eq 1 ]; then
        echo "Some patches failed. Aborting."
        exit 1
    fi
}

backup_ndk_file() {
    local file="$1"
    if [ -f "$file" ] || [ -L "$file" ]; then
        local rel_path="${file#$ANDROID_SDK/}"
        local backup_target="$BACKUP_DIR/ndk_backups/$rel_path"
        if [ ! -f "$backup_target" ] && [ ! -L "$backup_target" ]; then
            mkdir -p "$(dirname "$backup_target")"
            cp -a "$file" "$backup_target" 2>/dev/null || true
        fi
    fi
}

restore_ndk_backups() {
    if [ -d "$BACKUP_DIR/ndk_backups" ]; then
        echo "  Restoring NDK backups..."
        (
            cd "$BACKUP_DIR/ndk_backups"
            find . -type f -o -type l | while read -r rel; do
                local dest="$ANDROID_SDK/$rel"
                mkdir -p "$(dirname "$dest")"
                cp -a "$rel" "$dest" 2>/dev/null || true
                echo "  ✓ Restored $dest"
            done
        )
    fi
}

rollback_patches() {
    for patch_name in "${PATCH_ORDER[@]}"; do
        local target_file="${STATE_TARGET[$patch_name]}"
        if [ "${STATE_STATUS[$patch_name]}" == "applied" ]; then
            if [ -f "$BACKUP_DIR/$patch_name.orig" ]; then
                cp "$BACKUP_DIR/$patch_name.orig" "$target_file"
                echo "  ✓ $patch_name: rolled back"
                STATE_STATUS["$patch_name"]="rolled_back"
            else
                echo "  ✗ $patch_name: backup not found!"
            fi
        fi
    done
    restore_ndk_backups
    save_state
}

# --- Register Patches ---

patch_compile_sdk() {
    if grep -F -q "val compileSdkVersion: Int = 34" "$1"; then return 0; fi
    grep -q "val compileSdkVersion: Int =" "$1" || return 1
    sed -i 's/val compileSdkVersion: Int = [0-9]*/val compileSdkVersion: Int = 34/' "$1"
}
register_patch "compile_sdk" "$FLUTTER_ROOT/packages/flutter_tools/gradle/src/main/kotlin/FlutterExtension.kt" patch_compile_sdk

patch_plugin_constants() {
    if grep -F -q "PLATFORM_ARM64" "$1" && ! grep -F -q "PLATFORM_ARM32 = \"android-arm\"" "$1"; then return 0; fi
    cat > "$1" << 'INNER_EOF'
package com.flutter.gradle

object FlutterPluginConstants {
    private const val PLATFORM_ARM32 = "android-arm"
    private const val PLATFORM_ARM64 = "android-arm64"
    private const val PLATFORM_X86_64 = "android-x64"

    private const val ARCH_ARM32 = "armeabi-v7a"
    private const val ARCH_ARM64 = "arm64-v8a"
    private const val ARCH_X86_64 = "x86_64"

    const val INTERMEDIATES_DIR = "intermediates"
    const val FLUTTER_STORAGE_BASE_URL = "FLUTTER_STORAGE_BASE_URL"
    const val DEFAULT_MAVEN_HOST = "https://storage.googleapis.com"

    @JvmStatic val PLATFORM_ARCH_MAP =
        mapOf(
            PLATFORM_ARM32 to ARCH_ARM32,
            PLATFORM_ARM64 to ARCH_ARM64,
            PLATFORM_X86_64 to ARCH_X86_64
        )

    @JvmStatic val ABI_VERSION =
        mapOf(
            ARCH_ARM32 to 1,
            ARCH_ARM64 to 2,
            ARCH_X86_64 to 4
        )

    @JvmStatic val DEFAULT_PLATFORMS =
        listOf(
            PLATFORM_ARM64
        )

    @JvmStatic val PLATFORM_ABI_LIST: List<String> =
        DEFAULT_PLATFORMS.map { platform ->
            PLATFORM_ARCH_MAP[platform] ?: error("Invalid platform: $platform")
        }
}
INNER_EOF
}
register_patch "plugin_constants" "$FLUTTER_ROOT/packages/flutter_tools/gradle/src/main/kotlin/FlutterPluginConstants.kt" patch_plugin_constants

patch_build_apk() {
    if grep -F -q "['android-arm64']" "$1"; then return 0; fi
    grep -q "_kDefaultJitArchs" "$1" || return 1
    sed -i "s/static const _kDefaultJitArchs = <String>\['android-arm', 'android-arm64', 'android-x64'\]/static const _kDefaultJitArchs = <String>['android-arm64']/" "$1"
    sed -i "s/static const _kDefaultAotArchs = <String>\['android-arm', 'android-arm64', 'android-x64'\]/static const _kDefaultAotArchs = <String>['android-arm64']/" "$1"
}
register_patch "build_apk" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/commands/build_apk.dart" patch_build_apk

patch_build_aar() {
    if grep -F -q "defaultsTo: <String>['android-arm64']" "$1"; then return 0; fi
    grep -q "defaultsTo: <String>" "$1" || return 1
    sed -i "s/defaultsTo: <String>\['android-arm', 'android-arm64', 'android-x64'\]/defaultsTo: <String>['android-arm64']/" "$1"
}
register_patch "build_aar" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/commands/build_aar.dart" patch_build_aar

patch_build_appbundle() {
    if grep -F -q "defaultsTo: <String>['android-arm64']" "$1"; then return 0; fi
    grep -q "defaultsTo: <String>" "$1" || return 1
    sed -i "s/defaultsTo: <String>\['android-arm', 'android-arm64', 'android-x64'\]/defaultsTo: <String>['android-arm64']/" "$1"
}
register_patch "build_appbundle" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/commands/build_appbundle.dart" patch_build_appbundle

patch_plugin_utils() {
    # forceNdkDownload() patched to early return: Termux ships its own NDK, so Flutter's synthetic
    # NDK/CMake provisioning must never run for it. The early return below has to stay a RUNTIME
    # condition: a bare `return` there makes the rest of the function unreachable, and Kotlin then
    # refuses the smart cast on `androidComponents`
    #   "only safe (?.) or non-null asserted (!!.) calls are allowed on a nullable receiver of
    #    type 'AndroidComponentsExtension<*, *, *>?'"
    # which fails :gradle:compileKotlin and with it every `flutter build apk` (BUILD FAILED).
    # Reproduced with the real Kotlin compiler: bare return -> error, guarded return -> compiles.
    local guarded='        if (System.getenv("TERMUX_NDK_PROVISIONING") == null) return // Termux: NDK already installed, skip CMake trick'
    local bare='        return // Termux: NDK already installed, skip CMake trick'
    if grep -F -q "$guarded" "$1"; then return 0; fi
    if grep -F -q "$bare" "$1"; then
        # Installs patched before the smart-cast fix: drop the unconditional return first, then
        # re-insert the guarded form below.
        awk -v drop="$bare" '$0 != drop' "$1" > "$1.termux_tmp" && mv "$1.termux_tmp" "$1"
    fi
    grep -q "fun forceNdkDownload" "$1" || return 1
    awk -v ins="$guarded" '
        !inserted && /val forcingNotRequired: Boolean/ { print ins; inserted = 1 }
        { print }
    ' "$1" > "$1.termux_tmp" && mv "$1.termux_tmp" "$1"
    grep -F -q "$guarded" "$1"
}
register_patch "plugin_utils" "$FLUTTER_ROOT/packages/flutter_tools/gradle/src/main/kotlin/FlutterPluginUtils.kt" patch_plugin_utils

patch_flutter_cache() {
    local file="$1"
    local font_preimage='final List<String>? binaryDirs = artifacts[_platform.operatingSystem];'
    local font_postimage='final List<String>? binaryDirs = artifacts[_platform.isAndroid ? "linux" : _platform.operatingSystem]; // Termux: map Android host to Linux artifacts.'
    local font_legacy_single="final List<String>? binaryDirs = artifacts[_platform.isAndroid ? 'linux' : _platform.operatingSystem]; // Termux: map Android host to Linux artifacts"
    local font_legacy_single_period="${font_legacy_single}."
    local font_legacy_double="${font_postimage%.}"
    local file_dir work_tmp result_tmp class_content
    local host_declaration="    final bool termuxLinuxArm64Host = cache.getHostPlatformArchName() == 'linux-arm64';"
    local dart_sdks_declaration="    final List<List<String>> termuxDartSdks = termuxLinuxArm64Host ? _dartSdks.where((sdk) => sdk.first != 'linux-x64').toList() : _dartSdks;"
    local linux_spread="        if (!termuxLinuxArm64Host) ..._linuxBinaryDirs,"
    local dart_sdk_spread="        ...termuxDartSdks,"
    local linux_host_branch="      else if (_platform.isLinux && !termuxLinuxArm64Host)"

    file_dir=$(dirname "$file")
    work_tmp=$(mktemp "$file_dir/.flutter_cache.XXXXXX") || return 1
    result_tmp=$(mktemp "$file_dir/.flutter_cache_result.XXXXXX") || {
        rm -f "$work_tmp"
        return 1
    }
    if ! cp -p "$file" "$work_tmp"; then
        rm -f "$work_tmp" "$result_tmp"
        return 1
    fi

    # Stage both cache transformations in a same-directory copy. A failed or
    # incomplete migration must never leave only the FontSubset edit behind.
    if ! awk -v pre="$font_preimage" -v post="$font_postimage" \
        -v legacy_single="$font_legacy_single" -v legacy_single_period="$font_legacy_single_period" \
        -v legacy_double="$font_legacy_double" '
        {
            original = $0
            indentation = original
            sub(/[^[:space:]].*$/, "", indentation)
            normalized = original
            sub(/^[[:space:]]*/, "", normalized)
        }
        normalized == pre { print indentation post; recognized++; next }
        normalized == post || normalized == legacy_single || normalized == legacy_single_period || normalized == legacy_double {
            print original
            recognized++
            next
        }
        { print }
        END { if (recognized != 1) exit 1 }
    ' "$work_tmp" > "$result_tmp"; then
        echo "flutter_cache: unrecognized FontSubset cache selector" >&2
        rm -f "$work_tmp" "$result_tmp"
        return 1
    fi
    if ! cat "$result_tmp" > "$work_tmp"; then
        rm -f "$work_tmp" "$result_tmp"
        return 1
    fi

    if ! grep -F -q 'termuxLinuxArm64Host' "$work_tmp"; then
        if ! awk -v host_declaration="$host_declaration" -v dart_sdks_declaration="$dart_sdks_declaration" \
        -v linux_spread="$linux_spread" -v dart_sdk_spread="$dart_sdk_spread" \
        -v linux_host_branch="$linux_host_branch" '
        /^class AndroidGenSnapshotArtifacts extends EngineCachedArtifact \{/ { in_class = 1 }
        in_class && /^  List<List<String>> getBinaryDirs\(\) \{$/ {
            print
            print host_declaration
            print dart_sdks_declaration
            get_binary_dirs++
            next
        }
        in_class && /^        \.\.\._linuxBinaryDirs,$/ {
            linux_spread_count++
            if (linux_spread_count == 1) print linux_spread
            else print
            next
        }
        in_class && /^        \.\.\._dartSdks,$/ {
            print dart_sdk_spread
            dart_sdk_spread_count++
            next
        }
        in_class && /^      else if \(_platform\.isLinux\)$/ {
            print linux_host_branch
            linux_host_branch_count++
            next
        }
        in_class && /^}$/ { in_class = 0; class_count++ }
        { print }
        END {
            if (class_count != 1 || get_binary_dirs != 1 || linux_spread_count != 2 ||
                dart_sdk_spread_count != 1 || linux_host_branch_count != 1 || in_class) exit 1
        }
    ' "$work_tmp" > "$result_tmp"; then
            echo "flutter_cache: unsupported AndroidGenSnapshotArtifacts layout" >&2
            rm -f "$work_tmp" "$result_tmp"
            return 1
        fi
        if ! cat "$result_tmp" > "$work_tmp"; then
            rm -f "$work_tmp" "$result_tmp"
            return 1
        fi
    fi

    class_content=$(awk '
        /^class AndroidGenSnapshotArtifacts extends EngineCachedArtifact \{$/ {
            if (inside_class) bad = 1
            inside_class = 1
            class_count++
        }
        inside_class { print }
        inside_class && /^}$/ { inside_class = 0 }
        END { if (class_count != 1 || inside_class || bad) exit 1 }
    ' "$work_tmp") || {
        echo "flutter_cache: AndroidGenSnapshotArtifacts class is missing or malformed" >&2
        rm -f "$work_tmp" "$result_tmp"
        return 1
    }
    local host_count dart_sdks_count linux_spread_count raw_linux_spread_count
    local dart_sdk_spread_count linux_host_count direct_linux_spread_count
    host_count=$(printf '%s\n' "$class_content" | grep -Fxc -- "$host_declaration" || true)
    dart_sdks_count=$(printf '%s\n' "$class_content" | grep -Fxc -- "$dart_sdks_declaration" || true)
    linux_spread_count=$(printf '%s\n' "$class_content" | grep -Fxc -- "$linux_spread" || true)
    raw_linux_spread_count=$(printf '%s\n' "$class_content" | grep -Fxc -- '        ..._linuxBinaryDirs,' || true)
    dart_sdk_spread_count=$(printf '%s\n' "$class_content" | grep -Fxc -- "$dart_sdk_spread" || true)
    linux_host_count=$(printf '%s\n' "$class_content" | grep -Fxc -- "$linux_host_branch" || true)
    direct_linux_spread_count=$(printf '%s\n' "$class_content" | awk \
        -v branch="$linux_host_branch" \
        -v raw_spread='        ..._linuxBinaryDirs,' \
        -v gated_spread="$linux_spread" '
        $0 == branch { pending = 1; next }
        pending {
            if ($0 == raw_spread || $0 == gated_spread) matched++
            pending = 0
        }
        END { print matched + 0 }
    ')
    local linux_selection_valid=0
    if { [ "$linux_spread_count" -eq 1 ] && [ "$raw_linux_spread_count" -eq 1 ] && \
        [ "$direct_linux_spread_count" -eq 1 ]; } || \
        { [ "$linux_spread_count" -eq 2 ] && [ "$raw_linux_spread_count" -eq 0 ] && \
            [ "$direct_linux_spread_count" -eq 1 ]; }; then
        linux_selection_valid=1
    fi
    if [ "$host_count" -ne 1 ] || [ "$dart_sdks_count" -ne 1 ] || \
        [ "$linux_selection_valid" -ne 1 ] || [ "$dart_sdk_spread_count" -ne 1 ] || \
        [ "$linux_host_count" -ne 1 ] || \
        printf '%s\n' "$class_content" | grep -Fq '        ..._dartSdks,'; then
        echo "flutter_cache: incomplete AndroidGenSnapshotArtifacts ARM64 selection" >&2
        rm -f "$work_tmp" "$result_tmp"
        return 1
    fi

    rm -f "$result_tmp"
    if cmp -s "$file" "$work_tmp"; then
        rm -f "$work_tmp"
        return 0
    fi
    mv -f -- "$work_tmp" "$file"
}
register_patch "flutter_cache" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/flutter_cache.dart" patch_flutter_cache

patch_artifacts() {
    if grep -F -q "platform.isAndroid" "$1"; then return 0; fi
    grep -q "if (platform.isLinux)" "$1" || return 1
    sed -i "s#if (platform.isLinux) {#if (platform.isLinux || platform.isAndroid) { // Termux: map Android host to Linux artifacts.#" "$1"
}
register_patch "artifacts" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/artifacts.dart" patch_artifacts

patch_build_info() {
    if grep -F -q "globals.platform.isAndroid" "$1"; then return 0; fi
    grep -q "if (globals.platform.isLinux)" "$1" || return 1
    sed -i "s#if (globals.platform.isLinux) {#if (globals.platform.isLinux || globals.platform.isAndroid) { // Termux: Android host uses Linux artifacts.#" "$1"
}
register_patch "build_info" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/build_info.dart" patch_build_info

patch_chrome() {
    # Termux ships Chromium as `chromium-browser`, not `google-chrome`. A hardcoded `google-chrome`
    # lookup makes `flutter doctor` report the browser missing and `flutter run -d chrome` unusable
    # on a stock install, so the tool has to probe PATH for every known Chromium name instead.
    #
    # The idempotency guard tests the NEW marker, not `platform.isAndroid`: installs patched by the
    # previous version of this function already carry that line, so guarding on it would classify
    # them as "already applied" and the candidate list would never reach anyone who is already on
    # the released deb. patch_state.json's func_digest is what re-evaluates this function on those
    # installs (see AGENTS.md, invariant 6).
    if grep -F -q "kLinuxExecutableCandidates" "$1"; then return 0; fi
    grep -q "^const kLinuxExecutable = .google-chrome.;$" "$1" || return 1
    grep -q "^    return kLinuxExecutable;$" "$1" || return 1
    grep -q "is not supported" "$1" || return 1

    local consts helper
    consts=$(mktemp "$TMPDIR/patch_chrome_consts.XXXXXX")
    helper=$(mktemp "$TMPDIR/patch_chrome_helper.XXXXXX")

    cat > "$consts" << 'CHROME_CONSTS_EOF'
/// Termux: the name Termux packages Google Chrome/Chromium under.
const kTermuxExecutable = 'chromium-browser';

/// Termux: every Chromium name we accept on a Linux/Android host, in probe order.
///
/// Termux ships Chromium as `chromium-browser`, so a plain `google-chrome` lookup
/// makes `flutter doctor` and `flutter run -d chrome` fail on a stock install.
const List<String> kLinuxExecutableCandidates = <String>[
  kLinuxExecutable,
  kTermuxExecutable,
  'chromium',
  'chrome',
];

CHROME_CONSTS_EOF

    cat > "$helper" << 'CHROME_HELPER_EOF'
/// Termux: return the first of [candidates] that exists in `PATH`, or null.
///
/// Candidates are probed in order, so an explicitly installed `google-chrome` still
/// wins over the `chromium-browser` that ships with Termux.
String? firstExecutableOnPath(Platform platform, FileSystem fileSystem, List<String> candidates) {
  final String? path = platform.environment['PATH'];
  if (path == null || path.isEmpty) {
    return null;
  }
  final List<String> entries = path.split(':').where((String entry) => entry.isNotEmpty).toList();
  for (final String candidate in candidates) {
    final bool found = entries.any((String entry) => fileSystem.file(fileSystem.path.join(entry, candidate)).existsSync());
    if (found) {
      return candidate;
    }
  }
  return null;
}

CHROME_HELPER_EOF

    # The anchors are exactly the ones patches/3.47.2/flutter_sdk_arm64_default.patch touches, so an
    # install patched here at runtime ends up byte-identical to one built with the patch already in.
    awk -v consts="$consts" -v helper="$helper" '
        function emit(file,   line) { while ((getline line < file) > 0) print line; close(file) }
        /^\/\/\/ The expected executable name on macOS\.$/ { emit(consts); done_consts = 1 }
        $0 == "    return kLinuxExecutable;" {
            print "    // Termux: probe PATH for every known Chromium name instead of assuming `google-chrome`."
            print "    return firstExecutableOnPath(platform, fileSystem, kLinuxExecutableCandidates) ?? kLinuxExecutable;"
            done_probe = 1
            next
        }
        /^\/\/\/ Find the Microsoft Edge executable on the current platform\.$/ { emit(helper); done_helper = 1 }
        { print }
        END { if (!done_consts || !done_probe || !done_helper) exit 1 }
    ' "$1" > "$1.termux_tmp" || { rm -f "$consts" "$helper" "$1.termux_tmp"; return 1; }
    mv "$1.termux_tmp" "$1"
    rm -f "$consts" "$helper"

    sed -i "s#if (platform.isLinux) {#if (platform.isLinux || platform.isAndroid) { // Termux: use Linux Chrome lookup on Android host.#" "$1"
    grep -F -q "kLinuxExecutableCandidates" "$1"
}
register_patch "chrome" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/web/chrome.dart" patch_chrome

patch_build_linux() {
    if grep -F -q "false /* Termux" "$1"; then return 0; fi
    grep -q "if (!globals.platform.isLinux)" "$1" || return 1
    sed -i "s@if (!globals.platform.isLinux)@if (false /* Termux: allow linux build */)@" "$1"
    sed -i "s@!featureFlags.isLinuxEnabled || !globals.platform.isLinux@!featureFlags.isLinuxEnabled /* Termux: visible */@" "$1"
}
register_patch "build_linux" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/commands/build_linux.dart" patch_build_linux

patch_icon_tree_shaker() {
    if grep -F -q "false /* Termux" "$1"; then return 0; fi
    grep -q "kIconTreeShakerFlag" "$1" || return 1
    sed -i "s|_environment.defines\[kIconTreeShakerFlag\] == 'true'|false /* Termux: const_finder unavailable */|g" "$1"
}
register_patch "icon_tree_shaker" "$FLUTTER_ROOT/packages/flutter_tools/lib/src/build_system/targets/icon_tree_shaker.dart" patch_icon_tree_shaker

patch_cmake_lists() {
    if grep -F -q "FlutterNDKTrick" "$1"; then return 0; fi
    cat > "$1" << 'CMAKEOF'
cmake_minimum_required(VERSION 3.6)
project(FlutterNDKTrick C CXX)
CMAKEOF
}
register_patch "cmake_lists" "$FLUTTER_ROOT/packages/flutter_tools/gradle/src/main/scripts/CMakeLists.txt" patch_cmake_lists

patch_shebang_flutter() { if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi; sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"; }
patch_shebang_dart() { if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi; sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"; }
patch_shebang_shared() { if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi; sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"; }
patch_shebang_update_dart() { if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi; sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"; }
patch_shebang_content_hash() { if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi; sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"; }
patch_shebang_last_engine() { if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi; sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"; }
patch_shebang_update_engine() { if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi; sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"; }
patch_shebang_tool_backend() { if grep -F -q "#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash" "$1"; then return 0; fi; sed -i "1s|#!/usr/bin/env bash|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$1"; }

register_patch "shebang_flutter" "$FLUTTER_ROOT/bin/flutter" patch_shebang_flutter
register_patch "shebang_dart" "$FLUTTER_ROOT/bin/dart" patch_shebang_dart
register_patch "shebang_shared" "$FLUTTER_ROOT/bin/internal/shared.sh" patch_shebang_shared
register_patch "shebang_update_dart" "$FLUTTER_ROOT/bin/internal/update_dart_sdk.sh" patch_shebang_update_dart
register_patch "shebang_content_hash" "$FLUTTER_ROOT/bin/internal/content_aware_hash.sh" patch_shebang_content_hash
register_patch "shebang_last_engine" "$FLUTTER_ROOT/bin/internal/last_engine_commit.sh" patch_shebang_last_engine
register_patch "shebang_update_engine" "$FLUTTER_ROOT/bin/internal/update_engine_version.sh" patch_shebang_update_engine
register_patch "shebang_tool_backend" "$FLUTTER_ROOT/packages/flutter_tools/bin/tool_backend.sh" patch_shebang_tool_backend

if [ "$MODE" == "rollback" ]; then
    echo "Rolling back patches..."
    rollback_patches
    exit 0
fi

if [ "$MODE" == "status" ] || [ "$MODE" == "check" ]; then
    echo "Checking patch status..."
    apply_patches
    exit 0
fi


# Helper function to setup NDK clang wrappers for any NDK version
setup_ndk_clang_wrappers() {
    local NDK_PATH="$1"
    local NDK_NAME=$(basename "$NDK_PATH")

    if [ ! -d "$NDK_PATH/toolchains/llvm" ]; then
        echo "    ⚠ Skipping $NDK_NAME (no toolchains/llvm directory)"
        return
    fi

    local PREBUILT="$NDK_PATH/toolchains/llvm/prebuilt"
    local SYSROOT="$PREBUILT/linux-x86_64/sysroot"
    local CLANG_VERSION=$(ls -1 "$PREBUILT/linux-x86_64/lib/clang/" | sort -V | tail -n 1)
    local CLANG_LIB="$PREBUILT/linux-x86_64/lib/clang/$CLANG_VERSION/lib/linux"

    echo "    Setting up clang wrappers for NDK $NDK_NAME..."

    # Create wrapper script content (using NDK_PATH variable in script)
CLANG_WRAPPER="#!${PREFIX:-/data/data/com.termux/files/usr}/bin/sh
PREFIX=\"\${PREFIX:-${PREFIX:-/data/data/com.termux/files/usr}}\"
NDK=\"$NDK_PATH\"
SYSROOT=\"\$NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot\"
CLANG_VERSION=\$(ls -1 \"\$NDK/toolchains/llvm/prebuilt/linux-x86_64/lib/clang/\" 2>/dev/null | sort -V | tail -n 1)
CLANG_LIB=\"\$NDK/toolchains/llvm/prebuilt/linux-x86_64/lib/clang/\$CLANG_VERSION/lib/linux\"

ARCH=\"\"
for arg in \"\$@\"; do
    case \"\$arg\" in
        --target=aarch64*) ARCH=\"aarch64\" ;;
        --target=arm*) ARCH=\"arm\" ;;
    esac
done

if [ \"\$ARCH\" = \"aarch64\" ]; then
    LIB_PATH=\"\$SYSROOT/usr/lib/aarch64-linux-android\"
    CLANG_LIB_ARCH=\"\$CLANG_LIB/aarch64\"
elif [ \"\$ARCH\" = \"arm\" ]; then
    LIB_PATH=\"\$SYSROOT/usr/lib/arm-linux-androideabi\"
    CLANG_LIB_ARCH=\"\$CLANG_LIB/arm\"
else
    exec \"\$PREFIX/bin/clang\" \"\$@\"
fi

exec \"\$PREFIX/bin/clang\" -L\"\$LIB_PATH\" -L\"\$CLANG_LIB_ARCH\" \"\$@\""

CLANGPP_WRAPPER="#!${PREFIX:-/data/data/com.termux/files/usr}/bin/sh
PREFIX=\"\${PREFIX:-${PREFIX:-/data/data/com.termux/files/usr}}\"
NDK=\"$NDK_PATH\"
SYSROOT=\"\$NDK/toolchains/llvm/prebuilt/linux-x86_64/sysroot\"
CLANG_VERSION=\$(ls -1 \"\$NDK/toolchains/llvm/prebuilt/linux-x86_64/lib/clang/\" 2>/dev/null | sort -V | tail -n 1)
CLANG_LIB=\"\$NDK/toolchains/llvm/prebuilt/linux-x86_64/lib/clang/\$CLANG_VERSION/lib/linux\"

ARCH=\"\"
for arg in \"\$@\"; do
    case \"\$arg\" in
        --target=aarch64*) ARCH=\"aarch64\" ;;
        --target=arm*) ARCH=\"arm\" ;;
    esac
done

if [ \"\$ARCH\" = \"aarch64\" ]; then
    LIB_PATH=\"\$SYSROOT/usr/lib/aarch64-linux-android\"
    CLANG_LIB_ARCH=\"\$CLANG_LIB/aarch64\"
elif [ \"\$ARCH\" = \"arm\" ]; then
    LIB_PATH=\"\$SYSROOT/usr/lib/arm-linux-androideabi\"
    CLANG_LIB_ARCH=\"\$CLANG_LIB/arm\"
else
    exec \"\$PREFIX/bin/clang++\" \"\$@\"
fi

exec \"\$PREFIX/bin/clang++\" -L\"\$LIB_PATH\" -L\"\$CLANG_LIB_ARCH\" \"\$@\""

    # Create wrappers in prebuilt/bin/ (for some toolchain configs)
    mkdir -p "$PREBUILT/bin"
    backup_ndk_file "$PREBUILT/bin/clang"
    backup_ndk_file "$PREBUILT/bin/clang++"
    echo "$CLANG_WRAPPER" > "$PREBUILT/bin/clang"
    chmod +x "$PREBUILT/bin/clang"
    echo "$CLANGPP_WRAPPER" > "$PREBUILT/bin/clang++"
    chmod +x "$PREBUILT/bin/clang++"

    # Create wrappers in prebuilt/linux-x86_64/bin/ (official NDK structure)
    mkdir -p "$PREBUILT/linux-x86_64/bin"
    # Remove symlinks/files first (clang -> clang-18, clang++ -> clang chain causes overwrites)
    # Must use unlink to properly remove symlinks before writing
    for f in clang clang++; do
        backup_ndk_file "$PREBUILT/linux-x86_64/bin/$f"
        if [ -L "$PREBUILT/linux-x86_64/bin/$f" ] || [ -f "$PREBUILT/linux-x86_64/bin/$f" ]; then
            unlink "$PREBUILT/linux-x86_64/bin/$f" 2>/dev/null || rm "$PREBUILT/linux-x86_64/bin/$f" 2>/dev/null || true
        fi
    done
    echo "$CLANG_WRAPPER" > "$PREBUILT/linux-x86_64/bin/clang"
    chmod +x "$PREBUILT/linux-x86_64/bin/clang"
    echo "$CLANGPP_WRAPPER" > "$PREBUILT/linux-x86_64/bin/clang++"
    chmod +x "$PREBUILT/linux-x86_64/bin/clang++"

    # Create linux-aarch64 directory with bin subdirectory (for toolchain configs)
    # Note: Must NOT symlink linux-aarch64 -> bin because access to linux-aarch64/bin
    # would incorrectly resolve to bin/bin (which doesn't exist)
    rm -rf "$PREBUILT/linux-aarch64" 2>/dev/null || true
    mkdir -p "$PREBUILT/linux-aarch64/bin"
    cp "$PREBUILT/bin/clang" "$PREBUILT/linux-aarch64/bin/clang"
    cp "$PREBUILT/bin/clang++" "$PREBUILT/linux-aarch64/bin/clang++"

    # Create all API-level clang wrappers (required by Android Gradle Plugin)
    for api in 21 24 26 28 29 30 31 32 33 34 35; do
        ln -sf clang "$PREBUILT/linux-aarch64/bin/armv7a-linux-androideabi${api}-clang"
        ln -sf clang++ "$PREBUILT/linux-aarch64/bin/armv7a-linux-androideabi${api}-clang++"
        ln -sf clang "$PREBUILT/linux-aarch64/bin/aarch64-linux-android${api}-clang"
        ln -sf clang++ "$PREBUILT/linux-aarch64/bin/aarch64-linux-android${api}-clang++"
        ln -sf clang "$PREBUILT/linux-aarch64/bin/i686-linux-android${api}-clang"
        ln -sf clang++ "$PREBUILT/linux-aarch64/bin/i686-linux-android${api}-clang++"
        ln -sf clang "$PREBUILT/linux-aarch64/bin/x86_64-linux-android${api}-clang"
        ln -sf clang++ "$PREBUILT/linux-aarch64/bin/x86_64-linux-android${api}-clang++"
    done

    # Create sysroot symlink
    ln -sf linux-x86_64/sysroot "$PREBUILT/sysroot" 2>/dev/null || true

    # Patch toolchain cmake: skip compiler test and force ANDROID_HOST_TAG
    # Termux clang wrapper hangs on CMake compiler ID test, and host tag detection
    # returns empty string on Termux, causing sysroot path: prebuilt//sysroot
    local TOOLCHAIN="$NDK_PATH/build/cmake/android-legacy.toolchain.cmake"
    if [ -f "$TOOLCHAIN" ]; then
        backup_ndk_file "$TOOLCHAIN"
        if grep -q 'list(APPEND ANDROID_LINKER_FLAGS "-static-libstdc++")' "$TOOLCHAIN" 2>/dev/null; then
            sed -i 's/list(APPEND ANDROID_LINKER_FLAGS "-static-libstdc++")/# Disabled for Termux: list(APPEND ANDROID_LINKER_FLAGS "-static-libstdc++")/' "$TOOLCHAIN"
        fi
        if ! grep -q 'ANDROID_HOST_TAG' "$TOOLCHAIN" 2>/dev/null; then
            sed -i '1a set(ANDROID_HOST_TAG "linux-x86_64")' "$TOOLCHAIN"
        fi
    fi
    # Also patch the main android.toolchain.cmake
    local MAIN_TOOLCHAIN="$NDK_PATH/build/cmake/android.toolchain.cmake"
    if [ -f "$MAIN_TOOLCHAIN" ]; then
        backup_ndk_file "$MAIN_TOOLCHAIN"
        if ! grep -q 'ANDROID_HOST_TAG' "$MAIN_TOOLCHAIN" 2>/dev/null; then
            sed -i '1a set(ANDROID_HOST_TAG "linux-x86_64")' "$MAIN_TOOLCHAIN"
        fi
    fi

    # Replace x86_64 llvm-objcopy/llvm-strip with Termux ARM64 native binaries
    # (Gradle StripDebugSymbolsRunnable fails with x86_64 binaries on ARM64)
    local LLVM_BIN="$PREBUILT/linux-x86_64/bin"
    if [ -f "$PREFIX/bin/llvm-objcopy" ]; then
        backup_ndk_file "$LLVM_BIN/llvm-objcopy"
        backup_ndk_file "$LLVM_BIN/llvm-strip"
        cp "$PREFIX/bin/llvm-objcopy" "$LLVM_BIN/llvm-objcopy" 2>/dev/null || true
        cp "$PREFIX/bin/llvm-strip" "$LLVM_BIN/llvm-strip" 2>/dev/null || true
        echo "    ✓ llvm-objcopy/llvm-strip replaced with ARM64 native"
    fi

    echo "    ✓ NDK $NDK_NAME configured"
}

# Handle library mode (for unit testing / function sourcing)
if [ "$MODE" == "lib" ]; then
    return 0 2>/dev/null || exit 0
fi

# Handle read-only modes (--status, --check) and rollback mode (--rollback) immediately
if [ "$MODE" == "status" ] || [ "$MODE" == "check" ]; then
    echo "=== Post-install read-only report ($MODE) ==="
    apply_patches
    echo "=== Post-install read-only report finished ==="
    exit 0
fi

if [ "$MODE" == "rollback" ]; then
    echo "=== Rolling back post-install patches ==="
    rollback_patches
    echo "=== Rollback finished ==="
    exit 0
fi

# Run apply_patches for --apply
apply_patches

# 1.5b. Fix engine.stamp and engine.realm (required for Maven artifact resolution)
echo "[1.5b/13] Fixing engine.stamp and engine.realm, and injecting framework version tag..."
mkdir -p "$FLUTTER_ROOT/bin/cache"
[ -s "$FLUTTER_ROOT/bin/internal/engine.version" ] || echo -n "a804b261645ef8c13eb3d5c44a5c2fb0340c5539" > "$FLUTTER_ROOT/bin/internal/engine.version"
local_eng_ver="$(cat "$FLUTTER_ROOT/bin/internal/engine.version" 2>/dev/null | tr -d '\n\r')"
echo -n "$local_eng_ver" > "$FLUTTER_ROOT/bin/cache/engine.stamp" 2>/dev/null || true
echo -n "$local_eng_ver" > "$FLUTTER_ROOT/bin/cache/engine_stamp.stamp" 2>/dev/null || true
echo -n > "$FLUTTER_ROOT/bin/cache/engine.realm" 2>/dev/null || true

# Patch update_engine_version.sh to reliably read bin/internal/engine.version offline
if [ -f "$FLUTTER_ROOT/bin/internal/update_engine_version.sh" ]; then
    sed -i 's/elif.*ls-files.*engine.version.*/elif [ -f "$FLUTTER_ROOT\/bin\/internal\/engine.version" ]; then/' "$FLUTTER_ROOT/bin/internal/update_engine_version.sh" 2>/dev/null || true
fi

mkdir -p "$FLUTTER_ROOT/bin/cache/artifacts/material_fonts"
mkdir -p "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradle/wrapper"

# Populate real gradle wrapper if missing or 0 bytes
if [ ! -s "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradle/wrapper/gradle-wrapper.jar" ]; then
    echo "  Downloading Gradle Wrapper..."
    WRAPPER_REL_PATH=""
    if [ -f "$FLUTTER_ROOT/bin/internal/gradle_wrapper.version" ]; then
        WRAPPER_REL_PATH="$(cat "$FLUTTER_ROOT/bin/internal/gradle_wrapper.version" | tr -d '\n\r')"
    fi
    if [ -n "$WRAPPER_REL_PATH" ]; then
        cd "$TMPDIR"
        ( set +e; curl -s -L "https://storage.googleapis.com/$WRAPPER_REL_PATH" | tar -xz -C "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper" 2>/dev/null ) || true
        rm -f "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradle/wrapper/gradle-wrapper.properties" 2>/dev/null || true
        rm -f "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/NOTICE" 2>/dev/null || true
    fi
fi
if [ -f "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradlew" ]; then
    sed -i "1s|.*|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradlew" 2>/dev/null || true
    chmod 755 "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradlew" 2>/dev/null || true
else
    touch "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradlew" "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradlew.bat" "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradle/wrapper/gradle-wrapper.jar" 2>/dev/null || true
    sed -i "1s|.*|#!${PREFIX:-/data/data/com.termux/files/usr}/bin/bash|" "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradlew" 2>/dev/null || true
    chmod 755 "$FLUTTER_ROOT/bin/cache/artifacts/gradle_wrapper/gradlew" 2>/dev/null || true
fi

# Populate real material fonts if missing or empty
if [ ! -d "$FLUTTER_ROOT/bin/cache/artifacts/material_fonts" ] || [ -z "$(ls -A "$FLUTTER_ROOT/bin/cache/artifacts/material_fonts" 2>/dev/null)" ]; then
    echo "  Downloading Material Fonts..."
    FONTS_REL_PATH=""
    if [ -f "$FLUTTER_ROOT/bin/internal/material_fonts.version" ]; then
        FONTS_REL_PATH="$(cat "$FLUTTER_ROOT/bin/internal/material_fonts.version" | tr -d '\n\r')"
    fi
    if [ -n "$FONTS_REL_PATH" ]; then
        cd "$TMPDIR"
        ( set +e; curl -s -L "https://storage.googleapis.com/$FONTS_REL_PATH" -o fonts.zip 2>/dev/null && unzip -q -o fonts.zip -d "$FLUTTER_ROOT/bin/cache/artifacts/material_fonts" 2>/dev/null && rm -f fonts.zip ) || true
    fi
fi

for vfile in "$FLUTTER_ROOT"/bin/internal/*.version; do
    [ -f "$vfile" ] || continue
    vname=$(basename "$vfile" .version)
    cp "$vfile" "$FLUTTER_ROOT/bin/cache/${vname}.stamp" 2>/dev/null || true
done
cat > "$FLUTTER_ROOT/bin/cache/engine_stamp.json" << EOF
{
  "build_time_ms": 1770000000000,
  "git_revision": "$local_eng_ver",
  "git_revision_date": "2026-06-09T00:00:00Z",
  "content_hash": "$local_eng_ver"
}
EOF
echo "  ✓ engine.stamp=$(cat "$FLUTTER_ROOT/bin/cache/engine.stamp" 2>/dev/null || echo 'unknown')"
echo "  ✓ engine.realm cleared"

GIT_BIN="${PREFIX:-/data/data/com.termux/files/usr}/bin/git"
if ! [ -x "$GIT_BIN" ] && ! command -v "$GIT_BIN" >/dev/null 2>&1; then
    GIT_BIN="git"
fi

# Load canonical metadata from packaged manifest or embedded source-of-truth constants
CANONICAL_FLUTTER_VER="3.47.2"
CANONICAL_FRAMEWORK_REV="d3b14c876900e553bc736ca19295fc09e3853e8e"
CANONICAL_FRAMEWORK_DATE="2026-08-26 23:07:51 +0000"
CANONICAL_ENGINE_REV="$local_eng_ver"
CANONICAL_DART_VER="3.13.2"
CANONICAL_DEVTOOLS_VER="2.42.0"
CANONICAL_CHANNEL="stable"
CANONICAL_REPO_URL="https://github.com/flutter/flutter.git"

MANIFEST_LOADED=0
MANIFEST_FILE="$PREFIX/share/flutter/manifest.json"
if [ ! -f "$MANIFEST_FILE" ] && [ -f "$FLUTTER_ROOT/bin/cache/canonical_manifest.json" ]; then
    MANIFEST_FILE="$FLUTTER_ROOT/bin/cache/canonical_manifest.json"
fi

if [ -f "$MANIFEST_FILE" ]; then
    m_ver=$(grep -o '"flutter_version": *"[^"]*"' "$MANIFEST_FILE" 2>/dev/null | cut -d'"' -f4 || echo "")
    m_rev=$(grep -o '"framework_revision": *"[^"]*"' "$MANIFEST_FILE" 2>/dev/null | cut -d'"' -f4 || echo "")
    m_date=$(grep -o '"framework_commit_date": *"[^"]*"' "$MANIFEST_FILE" 2>/dev/null | cut -d'"' -f4 || echo "")
    m_eng=$(grep -o '"engine_revision": *"[^"]*"' "$MANIFEST_FILE" 2>/dev/null | cut -d'"' -f4 || echo "")
    m_dart=$(grep -o '"dart_version": *"[^"]*"' "$MANIFEST_FILE" 2>/dev/null | cut -d'"' -f4 || echo "")
    m_dev=$(grep -o '"devtools_version": *"[^"]*"' "$MANIFEST_FILE" 2>/dev/null | cut -d'"' -f4 || echo "")
    if [ -n "$m_ver" ]; then
        CANONICAL_FLUTTER_VER="$m_ver"
        MANIFEST_LOADED=1
    fi
    [ -n "$m_rev" ] && CANONICAL_FRAMEWORK_REV="$m_rev"
    [ -n "$m_date" ] && CANONICAL_FRAMEWORK_DATE="$m_date"
    [ -n "$m_eng" ] && CANONICAL_ENGINE_REV="$m_eng"
    [ -n "$m_dart" ] && CANONICAL_DART_VER="$m_dart"
    [ -n "$m_dev" ] && CANONICAL_DEVTOOLS_VER="$m_dev"
fi

# Fallback ONLY when package manifest is NOT present (e.g. legacy installation without manifest)
if [ "$MANIFEST_LOADED" -eq 0 ]; then
    if [ -f "$FLUTTER_ROOT/version" ]; then
        fixture_ver="$(cat "$FLUTTER_ROOT/version" | tr -d '\n\r')"
        if [ -n "$fixture_ver" ] && echo "$fixture_ver" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+'; then
            CANONICAL_FLUTTER_VER="$fixture_ver"
        fi
    elif [ -f "$FLUTTER_ROOT/.git/termux_synthetic" ]; then
        saved_synth_ver=$(grep -o '"version": *"[^"]*"' "$FLUTTER_ROOT/.git/termux_synthetic" 2>/dev/null | cut -d'"' -f4 || echo "")
        if [ -n "$saved_synth_ver" ] && echo "$saved_synth_ver" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+'; then
            CANONICAL_FLUTTER_VER="$saved_synth_ver"
        fi
    fi
fi

# Fail-closed change directory
cd "$FLUTTER_ROOT" || { echo "Error: Failed to cd to $FLUTTER_ROOT" >&2; exit 1; }

# Helper to identify if .git was created as a synthetic termux repository
is_synthetic_repo() {
    local target_dir="$1"
    local git_dir="$target_dir/.git"
    [ -d "$git_dir" ] || return 1
    if [ -f "$git_dir/termux_synthetic" ]; then
        return 0
    fi
    local commit_count
    commit_count=$("$GIT_BIN" --git-dir="$git_dir" rev-list --count HEAD 2>/dev/null || echo "999")
    local first_msg
    first_msg=$("$GIT_BIN" --git-dir="$git_dir" log -1 --pretty=%B 2>/dev/null || echo "")
    if [ "$commit_count" -le 2 ] && [ "$first_msg" = "Init framework" ]; then
        return 0
    fi
    return 1
}

# Synthetic repo setup & contamination repair
if ! [ -d "$FLUTTER_ROOT/.git" ]; then
    echo "  ! Missing .git, creating synthetic repository on stable branch for version resolution..."
    rm -f version
    "$GIT_BIN" init -q -b stable >/dev/null 2>&1 || "$GIT_BIN" init -q >/dev/null 2>&1
    "$GIT_BIN" symbolic-ref HEAD refs/heads/stable >/dev/null 2>&1 || true
    "$GIT_BIN" config user.email "termux@example.com"
    "$GIT_BIN" config user.name "termux"
    "$GIT_BIN" add -f bin/flutter bin/internal/engine.version bin/internal/*.version >/dev/null 2>&1 || true
    "$GIT_BIN" commit -q -m "Init framework" >/dev/null 2>&1 || true
    "$GIT_BIN" branch -M stable >/dev/null 2>&1 || true
    "$GIT_BIN" tag -f "$CANONICAL_FLUTTER_VER" HEAD >/dev/null 2>&1
    echo "{\"synthetic\":true,\"package\":\"termux-flutter-wsl\",\"version\":\"$CANONICAL_FLUTTER_VER\"}" > .git/termux_synthetic
    rm -f .git/FETCH_HEAD
    echo "  ✓ Synthetic tag $CANONICAL_FLUTTER_VER created on stable branch"
elif is_synthetic_repo "$FLUTTER_ROOT"; then
    echo "  ! Verifying and repairing synthetic repository state..."
    rm -f version
    # Normalize branch to stable if needed (handles master, main, trunk, develop, custom-name, detached HEAD)
    curr_br="$("$GIT_BIN" symbolic-ref --short HEAD 2>/dev/null || echo "")"
    if [ "$curr_br" != "stable" ]; then
        "$GIT_BIN" checkout -B stable >/dev/null 2>&1 || "$GIT_BIN" branch -M stable >/dev/null 2>&1 || "$GIT_BIN" symbolic-ref HEAD refs/heads/stable >/dev/null 2>&1
    fi

    # Purge any imported tag contamination (e.g. 1100+ upstream tags fetched)
    existing_tags=$("$GIT_BIN" tag -l 2>/dev/null || echo "")
    for t in $existing_tags; do
        if [ "$t" != "$CANONICAL_FLUTTER_VER" ]; then
            "$GIT_BIN" tag -d "$t" >/dev/null 2>&1 || true
        fi
    done
    head_tag=$("$GIT_BIN" tag --points-at HEAD 2>/dev/null || echo "")
    if ! echo "$head_tag" | grep -qx "$CANONICAL_FLUTTER_VER"; then
        "$GIT_BIN" tag -f "$CANONICAL_FLUTTER_VER" HEAD >/dev/null 2>&1
    fi
    rm -f .git/FETCH_HEAD
    echo "{\"synthetic\":true,\"package\":\"termux-flutter-wsl\",\"version\":\"$CANONICAL_FLUTTER_VER\"}" > .git/termux_synthetic
    echo "  ✓ Synthetic repository sanitized: branch=stable, tag=$CANONICAL_FLUTTER_VER (contamination purged)"
else
    # Non-synthetic / real user or upstream checkout: guardrail to protect user work
    echo "  ℹ Non-synthetic / user-owned Git checkout detected at $FLUTTER_ROOT; preserving git history and refs"
    if [ -n "$("$GIT_BIN" status --porcelain 2>/dev/null)" ]; then
        echo "    (Note: working tree contains modifications)"
    fi
fi

# Fail-closed postcondition verification for synthetic repos
if is_synthetic_repo "$FLUTTER_ROOT"; then
    verified_branch="$("$GIT_BIN" symbolic-ref --short HEAD 2>/dev/null || echo "unknown")"
    if [ "$verified_branch" != "stable" ]; then
        echo "Error: Synthetic repository branch verification failed: expected 'stable', got '$verified_branch'" >&2
        exit 1
    fi
    tags_at_head="$("$GIT_BIN" tag --points-at HEAD 2>/dev/null || echo "")"
    if ! echo "$tags_at_head" | grep -qx "$CANONICAL_FLUTTER_VER"; then
        echo "Error: Synthetic repository tag verification failed: '$CANONICAL_FLUTTER_VER' not pointing at HEAD" >&2
        exit 1
    fi
    tag_count="$("$GIT_BIN" tag -l 2>/dev/null | wc -l)"
    if [ "$tag_count" -ne 1 ]; then
        echo "Error: Synthetic repository tag count verification failed: expected 1 tag, found $tag_count" >&2
        exit 1
    fi
fi

# Determine and verify semantic Dart SDK version (never engine cache stamp)
EFFECTIVE_DART_VER=""
if [ -x "$DART_SDK/bin/dart" ]; then
    EFFECTIVE_DART_VER="$("$DART_SDK/bin/dart" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "")"
elif command -v dart >/dev/null 2>&1; then
    EFFECTIVE_DART_VER="$(dart --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "")"
fi

if [ -n "$EFFECTIVE_DART_VER" ]; then
    if [ "$EFFECTIVE_DART_VER" != "$CANONICAL_DART_VER" ]; then
        echo "Error: Installed Dart SDK version mismatch: expected '$CANONICAL_DART_VER', got '$EFFECTIVE_DART_VER'" >&2
        exit 1
    fi
else
    # Fallback to canonical when Dart binary is not executable in minimal mock fixtures
    EFFECTIVE_DART_VER="$CANONICAL_DART_VER"
fi

# Atomic generation and validation of canonical flutter.version.json
TMP_VER_JSON="$FLUTTER_ROOT/bin/cache/flutter.version.json.tmp.$$"
cat > "$TMP_VER_JSON" << EOF
{
  "frameworkVersion": "$CANONICAL_FLUTTER_VER",
  "channel": "$CANONICAL_CHANNEL",
  "repositoryUrl": "$CANONICAL_REPO_URL",
  "frameworkRevision": "$CANONICAL_FRAMEWORK_REV",
  "frameworkCommitDate": "$CANONICAL_FRAMEWORK_DATE",
  "engineRevision": "$CANONICAL_ENGINE_REV",
  "dartSdkVersion": "$EFFECTIVE_DART_VER",
  "devToolsVersion": "$CANONICAL_DEVTOOLS_VER",
  "flutterVersion": "$CANONICAL_FLUTTER_VER"
}
EOF

# Strict validation
if ! grep -q "\"frameworkVersion\": \"$CANONICAL_FLUTTER_VER\"" "$TMP_VER_JSON" || \
   ! grep -q "\"channel\": \"stable\"" "$TMP_VER_JSON" || \
   ! grep -q "\"repositoryUrl\": \"https://github.com/flutter/flutter.git\"" "$TMP_VER_JSON" || \
   ! grep -q "\"frameworkRevision\": \"$CANONICAL_FRAMEWORK_REV\"" "$TMP_VER_JSON" || \
   ! grep -q "\"engineRevision\": \"$CANONICAL_ENGINE_REV\"" "$TMP_VER_JSON" || \
   ! grep -q "\"dartSdkVersion\": \"$EFFECTIVE_DART_VER\"" "$TMP_VER_JSON" || \
   ! grep -q "\"devToolsVersion\": \"$CANONICAL_DEVTOOLS_VER\"" "$TMP_VER_JSON" || \
   ! grep -q "\"flutterVersion\": \"$CANONICAL_FLUTTER_VER\"" "$TMP_VER_JSON"; then
    echo "Error: Generated flutter.version.json failed canonical validation" >&2
    rm -f "$TMP_VER_JSON"
    exit 1
fi

mv -f "$TMP_VER_JSON" "$FLUTTER_ROOT/bin/cache/flutter.version.json"
chmod 644 "$FLUTTER_ROOT/bin/cache/flutter.version.json"
echo "  ✓ Canonical flutter.version.json generated ($CANONICAL_FLUTTER_VER stable, framework=$CANONICAL_FRAMEWORK_REV)"

# Get engine version for downloads
ENGINE_VERSION=$(cat $FLUTTER_ROOT/bin/internal/engine.version 2>/dev/null || echo "a804b261645ef8c13eb3d5c44a5c2fb0340c5539")

# 0. 下載官方 Dart SDK snapshots (修復 flutter run hot reload)
echo "[0/13] Downloading official Dart SDK snapshots (for hot reload)..."
SNAPSHOTS_URL="https://storage.googleapis.com/flutter_infra_release/flutter/${ENGINE_VERSION}/dart-sdk-linux-arm64.zip"
SNAPSHOTS_DIR=$DART_SDK/bin/snapshots

# Check if key snapshot is missing
if [ ! -f "$SNAPSHOTS_DIR/dds_aot.dart.snapshot" ]; then
    echo "  Downloading dart-sdk-linux-arm64.zip..."
    cd "$TMPDIR"
    ( set +e; curl -L -o dart-sdk.zip "$SNAPSHOTS_URL" >/dev/null 2>&1 ) || true
    if [ -f dart-sdk.zip ]; then
        echo "  Extracting snapshots..."
        unzip -o -j dart-sdk.zip 'dart-sdk/bin/snapshots/*' -d "$SNAPSHOTS_DIR" 2>/dev/null || true
        rm -f dart-sdk.zip
        echo "  ✓ Dart SDK snapshots installed"
    else
        echo "  ⚠ Network unavailable, skipping Dart SDK snapshots download"
    fi

    # Create symlinks for non-AOT versions
    ln -sf frontend_server_aot.dart.snapshot "$SNAPSHOTS_DIR/frontend_server.dart.snapshot" 2>/dev/null || true
else
    echo "  ✓ Dart SDK snapshots already exist"
fi

# 1. 清理 ELF 二進制的 DT_RPATH (修復 flutter run crash)
echo "[1/13] Cleaning ELF binaries (fix flutter run)..."
if ! command -v termux-elf-cleaner &> /dev/null; then
    ( set +e; pkg install -y termux-elf-cleaner >/dev/null 2>&1 ) || true
fi

# Clean dart binaries to remove DT_RPATH warnings that crash flutter run
if command -v termux-elf-cleaner &> /dev/null; then
    echo "  Cleaning dart-sdk binaries..."
    find $DART_SDK/bin -type f -executable 2>/dev/null | xargs -r termux-elf-cleaner 2>/dev/null || true

    echo "  Cleaning engine artifacts..."
    find $FLUTTER_ROOT/bin/cache/artifacts/engine -name "*.so" -o -name "gen_snapshot" -o -name "dart" 2>/dev/null | xargs -r termux-elf-cleaner 2>/dev/null || true

    echo "  ✓ ELF binaries cleaned"
else
    echo "  ⚠ termux-elf-cleaner not found, skipping"
fi

# 1.5d. Install Android SDK Platform 36 (Flutter 3.44.0 requirement)
echo "[1.5d/13] Installing Android SDK Platform 36..."
if [ ! -d "$ANDROID_SDK/platforms/android-36" ]; then
    mkdir -p $ANDROID_SDK/platforms
    cd $ANDROID_SDK/platforms
    ( set +e; curl -L -o platform-36.zip 'https://dl.google.com/android/repository/platform-36_r01.zip' >/dev/null 2>&1 ) || true
    if [ -f platform-36.zip ] && [ -s platform-36.zip ]; then
        unzip -q platform-36.zip 2>/dev/null || true
        rm -f platform-36.zip
        echo "  ✓ Platform 36 installed"
    else
        echo "  ⚠ Download skipped/unavailable for Platform 36"
    fi
    # Ensure no fake symlinks remain
    if [ -L "$ANDROID_SDK/platforms/android-36" ]; then
        rm -f "$ANDROID_SDK/platforms/android-36"
    fi
else
    echo "  ✓ Platform 36 already exists"
fi

# Install required Termux build dependencies
echo "[1.5e/13] Checking and installing Termux build dependencies..."

if ! command -v aapt2 &> /dev/null; then
    echo "  ! Termux aapt2 not found. Installing build dependencies via apt..."
    apt update >/dev/null 2>&1 || true
    apt install -y aapt2 libc++ libexpat openssl >/dev/null 2>&1 || true
fi

# Install d8/aidl/apksigner (required by AGP for build-tools validation)
for tool in d8 dx aidl apksigner zipalign; do
    if ! command -v $tool &> /dev/null; then
        echo "  ! $tool not found, installing..."
        apt install -y $tool >/dev/null 2>&1 || true
    fi
done

# Generate package_config.json for flutter_tools
# The flutter CLI runs flutter_tools.dart in JIT mode (see shared.sh line ~200)
# and requires .dart_tool/package_config.json from pub get.
echo "[1.5f/13] Generating flutter_tools package_config.json..."
FLUTTER_TOOLS_DIR=$FLUTTER_ROOT/packages/flutter_tools
PKG_CONFIG=$FLUTTER_TOOLS_DIR/.dart_tool/package_config.json

# Rewrite WSL build machine paths in prebuilt package_config.json to local FLUTTER_ROOT
if [ -f "$PKG_CONFIG" ]; then
    echo "  Rewriting package_config.json paths to $FLUTTER_ROOT..."
    sed -i "s|file://.*/flutter/|file://$FLUTTER_ROOT/|g" "$PKG_CONFIG" 2>/dev/null || true
    echo "  ✓ package_config.json paths updated"
fi

if [ ! -f "$PKG_CONFIG" ]; then
    echo "  Running pub get for flutter_tools..."
    cd "$FLUTTER_TOOLS_DIR"
    $DART_SDK/bin/dart pub get --offline --suppress-analytics >/dev/null 2>&1 || ( set +e; $DART_SDK/bin/dart pub get --suppress-analytics >/dev/null 2>&1 ) || true
    if [ -f "$PKG_CONFIG" ]; then
        echo "  ✓ package_config.json generated"
    else
        echo "  ⚠ Offline environment: package_config.json generation deferred"
    fi
fi

# 2. 下載並安裝 Android API 34 (aapt2 bug workaround)
echo "[2/13] Installing Android API 34..."
if [ ! -d "$ANDROID_SDK/platforms/android-34" ]; then
    cd $ANDROID_SDK/platforms
    ( set +e; curl -L -o platform-34.zip 'https://dl.google.com/android/repository/platform-34-ext7_r02.zip' >/dev/null 2>&1 ) || true
    if [ -f platform-34.zip ] && [ -s platform-34.zip ]; then
        unzip -q platform-34.zip 2>/dev/null || true
        rm -f platform-34.zip
        echo "  ✓ API 34 installed"
    else
        echo "  ⚠ API 34 download skipped (offline)"
    fi
else
    echo "  ✓ API 34 already exists"
fi

# Clear stale Gradle included-build outputs after changing the Flutter Gradle plugin.
# Without this, upgrades can compile FlutterPlugin.kt against an older cached
# FlutterPluginConstants.kt and fail with unresolved PLATFORM_ABI_LIST.
echo "  Clearing Flutter Gradle plugin build cache..."
rm -rf "$FLUTTER_ROOT/packages/flutter_tools/gradle/.gradle" \
       "$FLUTTER_ROOT/packages/flutter_tools/gradle/build" \
       "$FLUTTER_ROOT/packages/flutter_tools/gradle/bin" 2>/dev/null || true
echo "  ✓ Flutter Gradle plugin cache cleared"

# 3. 創建 NDK clang wrappers (處理所有已安裝的 NDK 版本)
echo "[4/13] Creating NDK clang wrappers..."

NDK_DIR="$ANDROID_SDK/ndk"
if [ -d "$NDK_DIR" ]; then
    NDK_COUNT=0
    for ndk_path in "$NDK_DIR"/*; do
        if [ -d "$ndk_path" ]; then
            setup_ndk_clang_wrappers "$ndk_path"
            NDK_COUNT=$((NDK_COUNT + 1))
        fi
    done
    if [ $NDK_COUNT -eq 0 ]; then
        echo "  ⚠ No NDK found. Clang wrappers will be created when NDK is installed."
        echo "    Re-run this script after installing NDK: bash $PREFIX/share/flutter/post_install.sh"
    else
        echo "  ✓ $NDK_COUNT NDK(s) configured"
    fi
else
    echo "  ⚠ NDK directory not found. Clang wrappers will be created when NDK is installed."
    echo "    Re-run this script after installing NDK: bash $PREFIX/share/flutter/post_install.sh"
fi

# Helper function to setup build-tools symlinks for any version
setup_build_tools_symlinks() {
    local BUILD_TOOLS="$1"
    local BT_NAME=$(basename "$BUILD_TOOLS")

    mkdir -p "$BUILD_TOOLS/lib"

    # Basic tools
    for tool in aapt aapt2 apksigner d8 dx zipalign aidl; do
        if [ "$tool" = "aapt2" ] && [ -L "$BUILD_TOOLS/aapt2" ] && readlink "$BUILD_TOOLS/aapt2" | grep -q "Android/Sdk"; then
            echo "    ✓ Retaining Mode B custom static aapt2 symlink"
        else
            ln -sf "$PREFIX/bin/$tool" "$BUILD_TOOLS/$tool" 2>/dev/null || true
        fi
    done

    # dexdump (from ART)
    if [ -f /apex/com.android.art/bin/dexdump ]; then
        ln -sf /apex/com.android.art/bin/dexdump "$BUILD_TOOLS/dexdump" 2>/dev/null || true
    fi

    # split-select stub
    if [ -L "$BUILD_TOOLS/split-select" ] && readlink "$BUILD_TOOLS/split-select" | grep -q "Android/Sdk"; then
        echo "    ✓ Retaining Mode B custom static split-select symlink"
    else
        cat > "$BUILD_TOOLS/split-select" << 'SPLITEOF'
#!/bin/sh
echo "split-select is not available on Termux ARM64"
exit 1
SPLITEOF
        chmod +x "$BUILD_TOOLS/split-select"
    fi

    # core-lambda-stubs.jar
    if [ ! -f "$BUILD_TOOLS/core-lambda-stubs.jar" ]; then
        MANIFEST_TMP="$TMPDIR/MANIFEST.MF"
        echo "Manifest-Version: 1.0" > "$MANIFEST_TMP"
        jar cfm "$BUILD_TOOLS/core-lambda-stubs.jar" "$MANIFEST_TMP" 2>/dev/null || true
        rm -f "$MANIFEST_TMP"
    fi

    # d8.jar and dx.jar
    ln -sf "$PREFIX/share/java/d8.jar" "$BUILD_TOOLS/lib/d8.jar" 2>/dev/null || true
    ln -sf "$PREFIX/share/java/d8.jar" "$BUILD_TOOLS/lib/dx.jar" 2>/dev/null || true

    echo "    ✓ build-tools $BT_NAME configured"
}

# 7. 創建 build-tools 符號連結 (for all versions)
echo "[8/13] Creating build-tools symlinks..."
BT_DIR=$ANDROID_SDK/build-tools
mkdir -p "$BT_DIR"

# If a real build-tools version exists (e.g. 35.0.0-2 from Termux),
# copy it as 35.0.0 so AGP can validate it (AGP rejects versions like 35.0.0-2)
BT_REAL=""
for bt in "$BT_DIR"/*/; do
    if [ -f "$bt/package.xml" ]; then
        BT_REAL="$bt"
        break
    fi
done

is_mode_b=false
if [ -L "$BT_DIR/35.0.0/aapt2" ] && readlink "$BT_DIR/35.0.0/aapt2" | grep -q "Android/Sdk"; then
    is_mode_b=true
fi

if [ "$is_mode_b" = "true" ]; then
    echo "  Validating Mode B toolchain (API 35+ / AAB)..."
    AAPT2_EXE="$BT_DIR/35.0.0/aapt2"
    TMP_DIR=$(mktemp -d "$TMPDIR/mode_b_test.XXXXXX")
    TMP_RES_DIR="$TMP_DIR/res/values"
    mkdir -p "$TMP_RES_DIR"
    echo '<resources><string name="test">test</string></resources>' > "$TMP_RES_DIR/strings.xml"
    TMP_FLAT_DIR="$TMP_DIR/flat"
    mkdir -p "$TMP_FLAT_DIR"
    TMP_APK="$TMP_DIR/test.apk"

    mode_b_valid=false
    if "$AAPT2_EXE" compile "$TMP_RES_DIR/strings.xml" -o "$TMP_FLAT_DIR/" >/dev/null 2>&1; then
        FLAT_FILE=$(find "$TMP_FLAT_DIR" -name "*.flat" 2>/dev/null | head -n 1)
        if [ -n "$FLAT_FILE" ]; then
            if "$AAPT2_EXE" link "$FLAT_FILE" -o "$TMP_APK" >/dev/null 2>&1 || [ -f "$TMP_APK" ]; then
                mode_b_valid=true
            fi
        fi
    fi
    rm -rf "$TMP_DIR"

    if [ "$mode_b_valid" = "false" ]; then
        echo "  ❌ Error: Mode B toolchain validation failed (aapt2 compile/link failed)."
        echo "  Reverting Mode B activation to Mode A..."

        # Reversible activation: Revert back to Mode A
        rm -rf "$BT_DIR/35.0.0" 2>/dev/null || true
        sed -i '/android.aapt2FromMavenOverride/d' "$HOME/.gradle/gradle.properties" 2>/dev/null || true

        echo "  Mode B reverted. Please install a working NDK (e.g. lzhiyong/termux-ndk) to use Mode B."
        is_mode_b=false
    else
        echo "  ✓ Mode B toolchain validation passed (aapt2 compile/link works)."
    fi
fi

if [ -n "$BT_REAL" ]; then
    if [ "$is_mode_b" = "false" ] && [ ! -f "$BT_DIR/35.0.0/package.xml" ]; then
        echo "  Cloning $(basename $BT_REAL) -> 35.0.0 (for AGP validation)..."
        rm -rf "$BT_DIR/35.0.0"
        cp -a "$BT_REAL" "$BT_DIR/35.0.0"
        BT_REAL_NAME=$(basename "$BT_REAL")
        sed -i "s/$BT_REAL_NAME/35.0.0/g" "$BT_DIR/35.0.0/source.properties" 2>/dev/null || true
        sed -i "s/$BT_REAL_NAME/35.0.0/g" "$BT_DIR/35.0.0/package.xml" 2>/dev/null || true
    elif [ "$is_mode_b" = "true" ] && [ ! -f "$BT_DIR/35.0.0/package.xml" ]; then
        echo "  Re-aligning package metadata for Mode B build-tools..."
        cp "$BT_REAL/package.xml" "$BT_DIR/35.0.0/" 2>/dev/null || true
        cp "$BT_REAL/source.properties" "$BT_DIR/35.0.0/" 2>/dev/null || true
        BT_REAL_NAME=$(basename "$BT_REAL")
        sed -i "s/$BT_REAL_NAME/35.0.0/g" "$BT_DIR/35.0.0/source.properties" 2>/dev/null || true
        sed -i "s/$BT_REAL_NAME/35.0.0/g" "$BT_DIR/35.0.0/package.xml" 2>/dev/null || true
    fi
fi

# Setup default version
setup_build_tools_symlinks "$BT_DIR/35.0.0"

# Create source.properties if missing (required by AGP)
if [ ! -f "$BT_DIR/35.0.0/source.properties" ]; then
    printf "Pkg.Revision=35.0.0\nPkg.Path=build-tools;35.0.0\nPkg.Desc=Android SDK Build-Tools 35\n" > "$BT_DIR/35.0.0/source.properties"
fi

# Also setup any other versions Gradle may have downloaded
for bt_path in "$BT_DIR"/*; do
    if [ -d "$bt_path" ] && [ "$(basename "$bt_path")" != "35.0.0" ]; then
        setup_build_tools_symlinks "$bt_path"
    fi
done

echo "  ✓ Build-tools symlinks created"

# 8. 安裝 cmdline-tools (讓 flutter 檢測 Android 設備)
echo "[9/13] Installing cmdline-tools..."
if [ ! -d "$ANDROID_SDK/cmdline-tools/latest" ]; then
    mkdir -p $ANDROID_SDK/cmdline-tools
    cd $ANDROID_SDK/cmdline-tools
    curl -L -o tools.zip 'https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip'
    unzip -q tools.zip
    mv cmdline-tools latest
    rm tools.zip
    echo "  ✓ cmdline-tools installed"
else
    echo "  ✓ cmdline-tools already exists"
fi

# 9. 創建 platform-tools 符號連結 (adb)
# Note: Gradle may download x86_64 platform-tools, so we force overwrite
echo "[10/13] Creating platform-tools symlinks..."
mkdir -p "$ANDROID_SDK/platform-tools"
# Remove any x86_64 binaries Gradle may have downloaded
rm -f "$ANDROID_SDK/platform-tools/adb" "$ANDROID_SDK/platform-tools/fastboot" 2>/dev/null || true
ln -sf "$PREFIX/bin/adb" "$ANDROID_SDK/platform-tools/adb" 2>/dev/null || true
ln -sf "$PREFIX/bin/fastboot" "$ANDROID_SDK/platform-tools/fastboot" 2>/dev/null || true
echo "  ✓ platform-tools symlinks created"

# 10. 接受 Android licenses
echo "[11/13] Accepting Android licenses..."
mkdir -p $ANDROID_SDK/licenses
echo -e "\n24333f8a63b6825ea9c5514f83c2829b004d1fee" > $ANDROID_SDK/licenses/android-sdk-license
echo -e "\n84831b9409646a918e30573bab4c9c91346d8abd" > $ANDROID_SDK/licenses/android-sdk-preview-license
echo "  ✓ Android licenses accepted"

# 10.5. Configure ANDROID_HOME in flutter config
echo "[11.5/13] Setting Android SDK path in Flutter config..."
mkdir -p "$HOME" 2>/dev/null || true
if [ -f "$HOME/.flutter_settings" ]; then
    grep -v '"android-sdk"' "$HOME/.flutter_settings" 2>/dev/null | grep -v '^[[:space:]]*}$' > "$HOME/.flutter_settings.tmp" 2>/dev/null || true
    echo '  ,"android-sdk": "'"$ANDROID_SDK"'"' >> "$HOME/.flutter_settings.tmp"
    echo '}' >> "$HOME/.flutter_settings.tmp"
    mv "$HOME/.flutter_settings.tmp" "$HOME/.flutter_settings" 2>/dev/null || true
else
    cat > "$HOME/.flutter_settings" << SETTINGS
{
  "android-sdk": "$ANDROID_SDK",
  "analytics": false
}
SETTINGS
fi
echo "  ✓ ANDROID_HOME=$ANDROID_SDK"

# 11. 複製 VM snapshots (for debug mode)
echo "[12/13] Checking engine artifacts..."
ENGINE_DIR=$FLUTTER_ROOT/bin/cache/artifacts/engine/linux-arm64

if [ ! -f "$ENGINE_DIR/vm_isolate_snapshot.bin" ]; then
    echo "  ⚠ vm_isolate_snapshot.bin not found - debug APK builds may fail"
    echo "    Please copy from WSL build: flutter/engine/src/out/linux_debug_arm64/gen/flutter/lib/snapshot/"
else
    echo "  ✓ VM snapshots present"
fi

# Remove only stale aliases that redirect linux-x64 Android snapshot downloads
# into packaged ARM64 generators. Never remove real x64 directories or unrelated links.
remove_stale_android_snapshot_aliases() {
    local engine_artifacts="$1"
    local directory alias packaged alias_target packaged_target
    for directory in android-arm64-release android-arm64-profile; do
        alias="$engine_artifacts/$directory/linux-x64"
        packaged="$engine_artifacts/$directory/linux-arm64"
        if [ -L "$alias" ] && [ -d "$packaged" ]; then
            alias_target=$(readlink -f "$alias" 2>/dev/null || true)
            packaged_target=$(readlink -f "$packaged" 2>/dev/null || true)
            if [ -n "$alias_target" ] && [ "$alias_target" = "$packaged_target" ]; then
                rm -f -- "$alias"
                echo "  ✓ Removed stale Android snapshot alias: $directory/linux-x64"
            fi
        fi
    done
    alias="$engine_artifacts/linux-x64"
    packaged="$engine_artifacts/linux-arm64"
    if [ -L "$alias" ] && [ -d "$packaged" ]; then
        alias_target=$(readlink -f "$alias" 2>/dev/null || true)
        packaged_target=$(readlink -f "$packaged" 2>/dev/null || true)
        if [ -n "$alias_target" ] && [ "$alias_target" = "$packaged_target" ]; then
            rm -f -- "$alias"
            echo "  ✓ Removed stale host artifact alias: linux-x64"
        fi
    fi
}
echo "[12.5/13] Removing stale Android snapshot aliases..."
ENG_ART=$FLUTTER_ROOT/bin/cache/artifacts/engine
remove_stale_android_snapshot_aliases "$ENG_ART"

# 12.7c. Create api-level.h for CMake system detection
# CMake's CMakeDetermineSystem.cmake reads $PREFIX/include/android/api-level.h
# Without this file, cmake fails with "file failed to open for reading"
echo "[12.7c/13] Creating api-level.h for CMake..."
mkdir -p "$PREFIX/include/android" 2>/dev/null
if [ ! -f "$PREFIX/include/android/api-level.h" ]; then
    cat > "$PREFIX/include/android/api-level.h" << 'HEADER'
#ifndef __ANDROID_API_LEVEL_H__
#define __ANDROID_API_LEVEL_H__
#define __ANDROID_API__ 35
#endif
HEADER
    echo "  ✓ api-level.h created"
else
    echo "  ✓ api-level.h already exists"
fi

# 13. Recompile flutter_tools.snapshot and stamp to guarantee offline flutter CLI execution
finalize_flutter_tools_cache() {
    echo "[13/13] Finalizing FlutterTools snapshot and stamp transactionally..."
    python3 - "$FLUTTER_ROOT" "$DART_SDK/bin/dart" "${FLUTTER_TOOL_ARGS:-}" "${TMPDIR:-$PREFIX/tmp}" <<'PY'
try:
    import fcntl
except ImportError:
    fcntl = None
import errno
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit


class FinalizerError(Exception):
    pass


class Interrupted(FinalizerError):
    def __init__(self, signum):
        self.signum = int(signum)
        super().__init__(f"Interrupted by signal {self.signum}")


def fsync_file(path):
    with open(path, "rb") as stream:
        os.fsync(stream.fileno())


def fsync_dir(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno not in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
                raise
    finally:
        os.close(descriptor)


def checked_run(command, env, description):
    process = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        _, stderr = process.communicate()
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    if process.returncode:
        raise FinalizerError(f"{description} failed (exit {process.returncode}): {stderr.strip()}")


def resolve_packages(config, root):
    try:
        data = json.loads(config.read_text())
    except (OSError, ValueError) as error:
        raise FinalizerError(f"Cannot read FlutterTools package config: {error}")
    if not isinstance(data, dict) or data.get("configVersion") != 2 or not isinstance(data.get("packages"), list):
        raise FinalizerError("FlutterTools package_config.json is invalid; refusing a package-less compile")
    roots = []
    for package in data["packages"]:
        uri = package.get("rootUri") if isinstance(package, dict) else None
        if not isinstance(uri, str):
            raise FinalizerError("FlutterTools package config contains an invalid package root")
        resolved_uri = urljoin(config.as_uri(), uri)
        parsed = urlsplit(resolved_uri)
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            raise FinalizerError(f"Unsupported FlutterTools package root URI: {uri}")
        try:
            package_root = Path(unquote(parsed.path)).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise FinalizerError(f"FlutterTools package root is missing or unsafe ({uri}): {error}")
        if not package_root.is_dir():
            raise FinalizerError(f"FlutterTools package root is missing or not a directory: {package_root}")
        roots.append((package, package_root))
    return data, roots


def isolated_tool_copy(root, config, packages, temp_root):
    isolated = temp_root / "sdk"
    isolated.mkdir(parents=True)
    (isolated / "bin" / "internal").mkdir(parents=True)
    (isolated / "bin" / "cache").mkdir(parents=True)
    try:
        # Dereference links into scratch copies so compiler inputs cannot reach live writable SDK paths.
        shutil.copytree(root / "packages", isolated / "packages", symlinks=False)
    except (OSError, shutil.Error, RuntimeError) as error:
        raise FinalizerError(f"Cannot safely copy FlutterTools source tree into isolation: {error}")
    tools = root / "packages" / "flutter_tools"
    for name in ("pubspec.yaml", "pubspec.lock"):
        if (tools / name).is_file():
            shutil.copy2(tools / name, isolated / "packages" / "flutter_tools" / name)
    for name in ("engine.version",):
        if (root / "bin" / "internal" / name).is_file():
            shutil.copy2(root / "bin" / "internal" / name, isolated / "bin" / "internal" / name)
    if (root / "version").is_file():
        shutil.copy2(root / "version", isolated / "version")
    for name in ("flutter.version.json", "engine.stamp", "engine.realm", "engine-dart-sdk.stamp",
                 "engine_stamp.json", "engine_stamp.stamp"):
        source = root / "bin" / "cache" / name
        if source.is_file():
            shutil.copy2(source, isolated / "bin" / "cache" / name)
    (isolated / "packages" / "flutter_tools" / ".dart_tool").mkdir(exist_ok=True)
    remapped = json.loads(json.dumps(packages))
    root_resolved = root.resolve()
    for index, (_, package_root) in enumerate(resolve_packages(config, root)[1]):
        try:
            relative = package_root.relative_to(root_resolved)
        except ValueError:
            relative = None
        if relative is not None:
            copied_root = isolated / relative
            if not copied_root.is_dir():
                raise FinalizerError(f"Package root was not copied into the isolated SDK: {package_root}")
        else:
            copied_root = isolated / ".packages" / str(index)
            copied_root.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copytree(package_root, copied_root, symlinks=False)
            except (OSError, shutil.Error, RuntimeError) as error:
                raise FinalizerError(f"Cannot safely copy external FlutterTools package root {package_root}: {error}")
        remapped["packages"][index]["rootUri"] = copied_root.as_uri() + "/"
    isolated_config = isolated / "packages" / "flutter_tools" / ".dart_tool" / "package_config.json"
    isolated_config.write_text(json.dumps(remapped) + "\n")
    shutil.copy2(config.parent.parent / "pubspec.yaml", isolated / "packages" / "flutter_tools" / "pubspec.yaml")
    shutil.copy2(config.parent.parent / "pubspec.lock", isolated / "packages" / "flutter_tools" / "pubspec.lock")
    fsync_file(isolated_config)
    return isolated, isolated_config


def recognized_absent_baseline_link(path, current, generations):
    expected = {
        "flutter_tools.snapshot": ".flutter_tools_current/flutter_tools.snapshot",
        "flutter_tools.stamp": ".flutter_tools_current/flutter_tools.stamp",
    }.get(path.name)
    if not expected or not path.is_symlink() or os.readlink(path) != expected or not current.is_symlink():
        return False
    try:
        baseline = current.resolve(strict=True)
        baseline.relative_to(generations.resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    marker = baseline / ".flutter_tools_baseline"
    return (baseline.parent == generations.resolve() and baseline.name.startswith("baseline-")
            and marker.is_file() and marker.read_bytes() == b"flutter-tools-baseline-v1\n"
            and not (baseline / path.name).exists())


def visible_file(path, current, generations):
    if path.is_symlink() and not path.exists():
        if recognized_absent_baseline_link(path, current, generations):
            return None
        raise FinalizerError(f"Refusing to replace unrecognized broken FlutterTools cache symlink: {path}")
    if not path.exists():
        return None
    if not path.is_file():
        raise FinalizerError(f"Refusing to replace non-file FlutterTools cache path: {path}")
    if path.is_symlink() and ".flutter_tools_current" not in os.readlink(path):
        raise FinalizerError(f"Refusing to replace unrecognized FlutterTools cache symlink: {path}")
    return path


def copy_metadata(source, destination):
    if source is None:
        os.chmod(destination, 0o644)
        return
    info = source.stat()
    os.chown(destination, info.st_uid, info.st_gid)
    os.chmod(destination, info.st_mode & 0o7777)
    os.utime(destination, ns=(info.st_atime_ns, info.st_mtime_ns))


def read_key(stamp):
    try:
        return stamp.read_text().rstrip("\n")
    except OSError:
        return None


def hash_input_path(digest, label, path, ancestors=()):
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (OSError, RuntimeError) as error:
        raise FinalizerError(f"Cannot fingerprint FlutterTools input {path}: {error}")
    kind = "directory" if resolved.is_dir() else "file" if resolved.is_file() else "other"
    digest.update(label.encode() + b"\0" + kind.encode() + b"\0")
    digest.update(str(info.st_mode & 0o7777).encode() + b"\0")
    if kind == "directory":
        identity = (info.st_dev, info.st_ino)
        if identity in ancestors:
            raise FinalizerError(f"FlutterTools source symlink cycle while fingerprinting: {path}")
        try:
            children = sorted(resolved.iterdir(), key=lambda child: child.name)
        except OSError as error:
            raise FinalizerError(f"Cannot read FlutterTools source directory {resolved}: {error}")
        for child in children:
            hash_input_path(digest, f"{label}/{child.name}", child, (*ancestors, identity))
    elif kind == "file":
        try:
            with resolved.open("rb") as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError as error:
            raise FinalizerError(f"Cannot read FlutterTools source file {resolved}: {error}")
    else:
        raise FinalizerError(f"Unsupported FlutterTools source input type: {resolved}")
    digest.update(b"\0")


def inputs_fingerprint(root, dart, config, tool_args):
    digest = hashlib.sha256()
    digest.update(b"flutter-tools-inputs-v1\0")
    digest.update("\0".join(tool_args).encode())
    package_data, packages = resolve_packages(config, root)
    hash_input_path(digest, "flutter_tools_source", root / "packages" / "flutter_tools")
    hash_input_path(digest, "package_config", config)
    hash_input_path(digest, "engine_version_stamp", root / "bin" / "cache" / "engine.stamp")
    hash_input_path(digest, "engine_stamp_metadata", root / "bin" / "cache" / "engine_stamp.json")
    hash_input_path(digest, "engine_stamp_cache_marker", root / "bin" / "cache" / "engine_stamp.stamp")
    seen = {str((root / "packages" / "flutter_tools").resolve())}
    for index, (_, package_root) in enumerate(packages):
        canonical = str(package_root.resolve())
        if canonical in seen:
            continue
        seen.add(canonical)
        hash_input_path(digest, f"package_root_{index}", package_root)
    hash_input_path(digest, "dart_compiler", dart)
    dartvm = dart.with_name("dartvm")
    if dartvm.is_file():
        hash_input_path(digest, "dart_vm", dartvm)
    return digest.hexdigest()


def validate_engine_stamp(path, engine_version_path, cache_stamp_path):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise FinalizerError(f"Flutter engine_stamp.json is missing or invalid; isolated version validation cannot safely run: {error}")
    if (not isinstance(data, dict) or type(data.get("build_time_ms")) is not int
            or not isinstance(data.get("git_revision"), str) or not data["git_revision"]
            or not isinstance(data.get("content_hash"), str) or not data["content_hash"]
            or not isinstance(data.get("git_revision_date"), str)):
        raise FinalizerError(f"Flutter engine_stamp.json is missing required fields: {path}")
    try:
        datetime.fromisoformat(data["git_revision_date"].replace("Z", "+00:00"))
    except ValueError as error:
        raise FinalizerError(f"Flutter engine_stamp.json has an invalid git_revision_date: {error}")
    try:
        engine_revision = engine_version_path.read_text().strip()
        cache_revision = cache_stamp_path.read_text().strip()
    except OSError as error:
        raise FinalizerError(f"Flutter engine_stamp cache markers are missing or unreadable: {error}")
    if not engine_revision or cache_revision != engine_revision:
        raise FinalizerError("Flutter engine_stamp cache marker does not match engine.stamp; refusing a possible HTTP fallback")
    if data["git_revision"] != engine_revision:
        raise FinalizerError("Flutter engine_stamp.json git_revision does not match engine.stamp")


def pair_valid(snapshot, stamp, key, yaml, lock, current, generations, fingerprint):
    if not (snapshot.is_file() and snapshot.stat().st_size > 0 and read_key(stamp) == key
            and yaml.is_file() and lock.is_file() and yaml.stat().st_mtime_ns <= lock.stat().st_mtime_ns
            and managed_layout(snapshot, stamp, current, generations)):
        return False
    manifest = current / "flutter_tools.inputs.sha256"
    return manifest.is_file() and manifest.read_text().strip() == f"v1:{fingerprint}"


def managed_layout(snapshot, stamp, current, generations):
    if not (snapshot.is_symlink() and stamp.is_symlink() and current.is_symlink()):
        return False
    if (os.readlink(snapshot) != ".flutter_tools_current/flutter_tools.snapshot"
            or os.readlink(stamp) != ".flutter_tools_current/flutter_tools.stamp"):
        return False
    try:
        current.resolve(strict=True).relative_to(generations.resolve())
    except (OSError, ValueError):
        return False
    return True


def atomic_symlink(target, link):
    temporary = link.with_name(link.name + ".new-" + uuid.uuid4().hex)
    os.symlink(target, temporary)
    try:
        os.replace(temporary, link)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    fsync_dir(link.parent)


def baseline_visible_pair(snapshot, stamp, current, generations, cache):
    baseline_stage = Path(tempfile.mkdtemp(prefix=".baseline-", dir=generations))
    visible = (visible_file(snapshot, current, generations), visible_file(stamp, current, generations))
    for source, name in zip(visible, ("flutter_tools.snapshot", "flutter_tools.stamp")):
        if source is not None:
            destination = baseline_stage / name
            shutil.copy2(source, destination)
            info = source.stat()
            os.chown(destination, info.st_uid, info.st_gid)
            os.chmod(destination, info.st_mode & 0o7777)
            os.utime(destination, ns=(info.st_atime_ns, info.st_mtime_ns))
            fsync_file(destination)
    marker = baseline_stage / ".flutter_tools_baseline"
    marker.write_bytes(b"flutter-tools-baseline-v1\n")
    fsync_file(marker)
    fsync_dir(baseline_stage)
    baseline = generations / ("baseline-" + uuid.uuid4().hex)
    os.replace(baseline_stage, baseline)
    fsync_dir(generations)
    atomic_symlink(os.path.relpath(baseline, cache), current)
    atomic_symlink(".flutter_tools_current/flutter_tools.snapshot", snapshot)
    atomic_symlink(".flutter_tools_current/flutter_tools.stamp", stamp)


def main():
    root = Path(sys.argv[1]).resolve()
    dart = Path(sys.argv[2])
    # shared.sh expands $FLUTTER_TOOL_ARGS unquoted, so its arguments use IFS word splitting.
    tool_args = sys.argv[3].split()
    temp_root = Path(sys.argv[4]).resolve()
    temp_root.mkdir(parents=True, exist_ok=True)
    cache = root / "bin" / "cache"
    tools = root / "packages" / "flutter_tools"
    snapshot = cache / "flutter_tools.snapshot"
    stamp = cache / "flutter_tools.stamp"
    config = tools / ".dart_tool" / "package_config.json"
    engine_stamp = cache / "engine_stamp.json"
    engine_version_stamp = cache / "engine.stamp"
    engine_stamp_cache_marker = cache / "engine_stamp.stamp"
    entry = tools / "bin" / "flutter_tools.dart"
    yaml = tools / "pubspec.yaml"
    lock = tools / "pubspec.lock"
    shared = root / "bin" / "internal" / "shared.sh"
    if fcntl is None:
        raise FinalizerError("Python fcntl.flock is unavailable; cannot safely coordinate with Flutter startup")
    validate_engine_stamp(engine_stamp, engine_version_stamp, engine_stamp_cache_marker)
    if not dart.is_file() or not os.access(dart, os.X_OK):
        raise FinalizerError(f"Dart compiler missing or not executable at {dart}")
    for required in (entry, config, yaml, lock, shared):
        if not required.is_file():
            raise FinalizerError(f"Required FlutterTools finalizer input is missing: {required}")
    if yaml.stat().st_mtime_ns > lock.stat().st_mtime_ns:
        raise FinalizerError("FlutterTools pubspec.lock is stale; resolve FlutterTools packages before finalization")
    package_data, _ = resolve_packages(config, root)
    revision = ""
    if (root / ".git").exists():
        git = shutil.which("git")
        if git:
            result = subprocess.run([git, "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
            if result.returncode == 0:
                revision = result.stdout.strip()
    if not revision:
        revision = os.environ.get("FLUTTER_PREBUILT_ENGINE_VERSION", "")
    if not revision and (root / "bin" / "internal" / "engine.version").is_file():
        revision = (root / "bin" / "internal" / "engine.version").read_text().strip()
    if not revision:
        raise FinalizerError("Failed to determine Flutter SDK revision")
    key = f"{revision}:{sys.argv[3]}"
    # Match the launcher lock: exclusive flock on shared.sh itself (its fd 7).
    with shared.open("rb") as lock_stream:
        deadline = time.monotonic() + 120
        while True:
            try:
                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise FinalizerError("Timed out waiting for Flutter shared.sh startup lock")
                time.sleep(0.1)

        current = cache / ".flutter_tools_current"
        generations = cache / ".flutter_tools_generations"
        generations.mkdir(parents=True, exist_ok=True)
        package_data, _ = resolve_packages(config, root)
        fingerprint = inputs_fingerprint(root, dart, config, tool_args)
        if pair_valid(snapshot, stamp, key, yaml, lock, current, generations, fingerprint):
            print(f"  ✓ Existing FlutterTools generation is current (key={key}, inputs={fingerprint})")
            return 0

        interrupted = []
        def on_signal(signum, _frame):
            interrupted.append(signum)
            raise Interrupted(signum)
        previous_signals = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGINT, signal.SIGTERM)}
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=generations))
        isolated_temp = Path(tempfile.mkdtemp(prefix="flutter-tools-check-", dir=temp_root))
        try:
            isolated_root, isolated_config = isolated_tool_copy(root, config, package_data, isolated_temp)
            isolated_entry = isolated_root / "packages" / "flutter_tools" / "bin" / "flutter_tools.dart"
            stage_snapshot = stage / "flutter_tools.snapshot"
            stage_stamp = stage / "flutter_tools.stamp"
            stage_manifest = stage / "flutter_tools.inputs.sha256"
            env = dict(os.environ)
            env.update({"FLUTTER_ROOT": str(isolated_root), "HOME": str(isolated_temp / "home"),
                        "PUB_CACHE": str(isolated_root / ".pub-cache"), "TMPDIR": str(isolated_temp / "tmp")})
            Path(env["HOME"]).mkdir(parents=True)
            Path(env["TMPDIR"]).mkdir()
            Path(env["PUB_CACHE"]).mkdir(exist_ok=True)
            compile_args = [str(dart), "--verbosity=error", *tool_args,
                            f"--snapshot={stage_snapshot}", "--snapshot-kind=kernel",
                            f"--packages={isolated_config}", "--no-enable-mirrors", str(isolated_entry), "--version"]
            checked_run(compile_args, env, "Failed to compile flutter_tools.snapshot")
            if interrupted:
                raise Interrupted(interrupted[-1])
            if not stage_snapshot.is_file() or stage_snapshot.stat().st_size == 0:
                raise FinalizerError("Dart returned success but produced an empty FlutterTools snapshot")
            stage_stamp.write_bytes(key.encode())
            if not stage_stamp.is_file() or not stage_stamp.stat().st_size or read_key(stage_stamp) != key:
                raise FinalizerError("Failed to write a valid staged flutter_tools.stamp")
            copy_metadata(visible_file(snapshot, cache / ".flutter_tools_current", cache / ".flutter_tools_generations"), stage_snapshot)
            copy_metadata(visible_file(stamp, cache / ".flutter_tools_current", cache / ".flutter_tools_generations"), stage_stamp)
            stage_manifest.write_text(f"v1:{fingerprint}\n")
            if stage_manifest.read_text().strip() != f"v1:{fingerprint}":
                raise FinalizerError("Failed to write a valid FlutterTools input manifest")
            fsync_file(stage_snapshot)
            fsync_file(stage_stamp)
            fsync_file(stage_manifest)
            fsync_dir(stage)
            # Load the candidate from the complete isolated tool tree; never run it against the live SDK.
            load_env = dict(env)
            load_env["FLUTTER_ROOT"] = str(isolated_root)
            checked_run([str(dart), f"--packages={isolated_config}", *tool_args,
                         str(stage_snapshot), "--no-version-check", "--suppress-analytics", "--version"],
                        load_env, "Isolated FlutterTools snapshot validation")
            if interrupted:
                raise Interrupted(interrupted[-1])
            if inputs_fingerprint(root, dart, config, tool_args) != fingerprint:
                raise FinalizerError("FlutterTools compiler inputs changed during staging; refusing publication")

            generation = generations / ("generation-" + uuid.uuid4().hex)
            os.replace(stage, generation)
            fsync_dir(generations)
            # First-time conversion/recovery: materialize the visible old pair with metadata preserved.
            managed = managed_layout(snapshot, stamp, current, generations)
            if not managed:
                baseline_visible_pair(snapshot, stamp, current, generations, cache)
            if inputs_fingerprint(root, dart, config, tool_args) != fingerprint:
                raise FinalizerError("FlutterTools compiler inputs changed before publication; refusing pointer switch")
            atomic_symlink(os.path.relpath(generation, cache), current)
            print(f"  ✓ FlutterTools generation published (key={key})")
            return 0
        finally:
            for sig, handler in previous_signals.items():
                signal.signal(sig, handler)
            shutil.rmtree(isolated_temp, ignore_errors=True)
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)


try:
    sys.exit(main())
except Interrupted as error:
    print(f"  ❌ Error: {error}; the published FlutterTools generation remains intact", file=sys.stderr)
    sys.exit(128 + error.signum)
except (FinalizerError, OSError, ValueError, KeyError) as error:
    print(f"  ❌ Error: {error}", file=sys.stderr)
    sys.exit(1)
PY
}

ensure_profile_env() {
    local profile_dir="${PREFIX:-/data/data/com.termux/files/usr}/etc/profile.d"
    local profile_file="$profile_dir/flutter.sh"
    mkdir -p "$profile_dir" 2>/dev/null || true
    if [ ! -f "$profile_file" ]; then
        echo "Creating environment profile script at $profile_file..."
        cat > "$profile_file" << 'EOF'
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
export PATH=${PREFIX}/opt/flutter/bin:${PATH}
: "${TMPDIR:=$PREFIX/tmp}"
export TMPDIR
mkdir -p "$TMPDIR"
if [ -z "${ANDROID_NDK_HOME:-}" ]; then
  for ndk in ${PREFIX}/opt/android-sdk/ndk/*/; do
    [ -d "$ndk" ] && export ANDROID_NDK_HOME="${ndk%/}" && break
  done
fi
if [ -z "${JAVA_HOME:-}" ] && [ -d "${PREFIX}/lib/jvm" ]; then
  _jvm=$(find "${PREFIX}/lib/jvm" -maxdepth 1 -type d -name 'java-*-openjdk' 2>/dev/null | sort -V | tail -1)
  [ -n "$_jvm" ] && export JAVA_HOME="$_jvm"
  unset _jvm
fi
EOF
        chmod 755 "$profile_file" 2>/dev/null || true
        echo "  ✓ Generated $profile_file"
    fi
}

finalize_flutter_tools_cache || { echo "❌ Failed to finalize flutter_tools cache" >&2; exit 1; }
ensure_profile_env

echo ""

echo "=========================================="
echo "Post-install configuration complete!"
echo "=========================================="
echo ""
echo "=== Quick Start ==="
echo "  source $PREFIX/etc/profile.d/flutter.sh"
echo "  flutter create myapp && cd myapp"
echo ""
echo "=== IMPORTANT: Project Setup (REQUIRED for each Flutter project) ==="
echo "  1. Fix gradlew shebang:"
echo "     sed -i '1s|#!/usr/bin/env bash|#!'"$PREFIX"'/bin/bash|' android/gradlew"
echo ""
echo "  2. Edit android/app/build.gradle.kts:"
echo "     compileSdk = 34"
echo "     targetSdk = 34"
echo "     ndk { abiFilters += listOf(\"arm64-v8a\") }"
echo ""
echo "  3. Add to android/gradle.properties:"
echo "     android.aapt2FromMavenOverride=$PREFIX/bin/aapt2"
echo "     (or run: useMyApt    # adds/replaces that line for you)"
echo ""
echo "  4. Set JAVA_HOME before building:"
echo "     export JAVA_HOME=\$(find \$PREFIX/lib/jvm -maxdepth 1 -type d -name 'java-*-openjdk' | sort -V | tail -1)"
echo ""
echo ""
echo "  5. Build APK:"
echo "     flutter build apk --release --target-platform android-arm64"
echo ""
echo "=== Linux Desktop Build (optional) ==="
echo "  1. Add to linux/CMakeLists.txt (first line, before cmake_minimum_required):"
echo "     set(CMAKE_SYSTEM_NAME Linux)"
echo ""
echo "  2. Build:"
echo "     flutter build linux --release"
echo ""
echo "=== Flutter Run (hot reload on device) ==="
echo "  1. Install android-tools:  pkg install android-tools"
echo "  2. Enable ADB TCP (from PC):  adb tcpip 5555"
echo "  3. Connect in Termux:  adb connect localhost:5555"
echo "     (Accept the 'Allow USB debugging?' dialog on screen)"
echo "  4. Run:  flutter run -d emulator-5554"
echo ""
