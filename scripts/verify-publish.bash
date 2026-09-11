#!/bin/bash
set -ueo pipefail

# Install the published package from an index and run what it installed, so a green
# publish means installable rather than uploaded.
#
# --prod reads PyPI; without it, TestPyPI.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly DIR

INDEX="https://test.pypi.org/simple/"
WHICH="TestPyPI"
if [ "${1:-}" = "--prod" ]; then
    INDEX="https://pypi.org/simple/"
    WHICH="PyPI"
fi
readonly INDEX WHICH

# 'uv version' prints "<name> <version>", which is both halves of what this needs. Reading
# pyproject.toml here would mean carrying a TOML parser for one line, which is what
# functions.bash used to do.
PROJECT="$(cd "$DIR/.." && uv version)"
readonly PROJECT
NAME="${PROJECT%% *}"
VERSION="${PROJECT##* }"
readonly NAME VERSION

echo "Verifying $NAME==$VERSION from $WHICH..." >&2

# --isolated and a throwaway cache, so a local build of the same version cannot answer for
# the index. --index-strategy unsafe-best-match because TestPyPI carries none of the
# dependencies.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# An index accepts an upload before it serves it, and the delay differs between the JSON
# API and the simple index resolved against here: 0.2.0 answered on the former while this
# still could not find it. So the wait is this resolution retried rather than a different
# endpoint polled, because this is the condition a green publish is claiming.
#
# Six attempts backing off from 15s is about 7.75 minutes, and it stops the moment the
# version resolves.
readonly ATTEMPTS=6
readonly BASE=15

# The version the installed command reports, compared against the one asked
# of the index. Running it and discarding the output would pass while every
# command on the machine printed a number from a previous release.
attempt=0
delay="$BASE"
while :; do
    attempt=$((attempt + 1))

    if REPORTED="$(UV_CACHE_DIR="$TMP/cache" uv tool run \
        --isolated \
        --index "$INDEX" \
        --index-strategy unsafe-best-match \
        --from "$NAME==$VERSION" \
        gw version 2>"$TMP/resolve.err")"; then
        break
    fi

    if [ "$attempt" -ge "$ATTEMPTS" ]; then
        cat "$TMP/resolve.err" >&2
        echo "$WHICH did not serve $NAME==$VERSION after $ATTEMPTS attempts." >&2
        exit 1
    fi

    echo "waiting ${delay}s for $WHICH to serve $NAME==$VERSION (attempt $attempt/$ATTEMPTS)..." >&2
    sleep "$delay"
    delay=$((delay * 2))
done
readonly REPORTED

[ "$REPORTED" = "$VERSION" ] || {
    echo "Version mismatch after publishing to $WHICH." >&2
    echo "  asked the index for: $VERSION" >&2
    echo "  gw version says:     $REPORTED" >&2
    exit 1
}

echo "$NAME==$VERSION installs from $WHICH and reports $REPORTED." >&2
