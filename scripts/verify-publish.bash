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

# The version the installed script reports, compared against the one asked
# of the index. Running it and discarding the output would pass while every
# command on the machine printed a number from a previous release.
REPORTED="$(UV_CACHE_DIR="$TMP/cache" uv tool run \
    --isolated \
    --index "$INDEX" \
    --index-strategy unsafe-best-match \
    --from "$NAME==$VERSION" \
    gws --version)"
readonly REPORTED

[ "$REPORTED" = "$VERSION" ] || {
    echo "Version mismatch after publishing to $WHICH." >&2
    echo "  asked the index for: $VERSION" >&2
    echo "  gws --version says:  $REPORTED" >&2
    exit 1
}

echo "$NAME==$VERSION installs from $WHICH and reports $REPORTED." >&2
