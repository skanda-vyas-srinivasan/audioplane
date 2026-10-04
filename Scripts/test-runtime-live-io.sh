#!/bin/sh
set -eu

# Explicit opt-in: real Process Taps, synthetic tone apps, installed loopback.
# No microphone recording, provider networking, driver installation, or route switching.
ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
RUNTIME="$ROOT_DIR/.build/signed-dev/bin/sonexis-runtime"
if [ ! -x "$RUNTIME" ]; then
    printf '%s\n' 'Build the signed Runtime first: ./Scripts/build-signed-runtime-dev.sh' >&2
    exit 2
fi
if [ -n "${AUDIOPLANE_TEST_PYTHON:-}" ]; then
    TEST_PYTHON="$AUDIOPLANE_TEST_PYTHON"
elif [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    TEST_PYTHON="$ROOT_DIR/.venv/bin/python"
else
    TEST_PYTHON=python3
fi
export PYTHONPATH="$ROOT_DIR/SDKs/python/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
exec "$TEST_PYTHON" -B "$ROOT_DIR/Tests/RuntimeLiveIO/check.py" --runtime "$RUNTIME" "$@"
