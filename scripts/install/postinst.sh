#!/data/data/com.termux/files/usr/bin/sh
# Debian maintainer script: configure only after the package is fully unpacked.

set -eu

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
case "${1:-}" in
    abort-*)
        exit 0
        ;;
    triggered)
        exit 0
        ;;
    configure)
        ;;
    *)
        echo "flutter postinst: unsupported action '${1:-<missing>}'" >&2
        exit 1
        ;;
esac

POST_INSTALL="$PREFIX/share/flutter/post_install.sh"
if [ ! -x "$POST_INSTALL" ]; then
    echo "flutter postinst: required bootstrap entrypoint is missing: $POST_INSTALL" >&2
    exit 1
fi

TERMUX_BASH="$PREFIX/bin/bash"
if [ ! -x "$TERMUX_BASH" ]; then
    echo "flutter postinst: required Termux Bash is missing: $TERMUX_BASH" >&2
    exit 1
fi

export PREFIX
export FLUTTER_ROOT="$PREFIX/opt/flutter"
export ANDROID_SDK="$PREFIX/opt/android-sdk"
if [ -z "${HOME:-}" ]; then
    echo "flutter postinst: HOME is not set; refusing to write user configuration" >&2
    exit 1
fi

unset LD_PRELOAD
exec "$TERMUX_BASH" "$POST_INSTALL" --package-bootstrap
