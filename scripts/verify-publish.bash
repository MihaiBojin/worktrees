#!/bin/bash
set -ueo pipefail

# Install the published package from an index and check what it reports, so a
# green publish means installable rather than uploaded.
#
# --prod reads PyPI; without it, TestPyPI.

INDEX="https://test.pypi.org/simple/"
HOST="https://test.pypi.org"
WHICH="TestPyPI"
if [ "${1:-}" = "--prod" ]; then
    INDEX="https://pypi.org/simple/"
    HOST="https://pypi.org"
    WHICH="PyPI"
fi
readonly INDEX HOST WHICH

NAME="$(uv version --output-format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["package_name"])')"
VERSION="$(uv version --short)"
readonly NAME VERSION

# An index takes time to serve what it has just accepted, and a short fixed
# wait fails releases that published correctly. Six attempts backing off from
# 15 seconds covers about 7.75 minutes.
rt net::await_url "$HOST/pypi/$NAME/$VERSION/json"

echo "Verifying $NAME==$VERSION from $WHICH..." >&2

# --isolated and a throwaway cache, so a local build of the same version
# cannot answer for the index. --index-strategy unsafe-best-match because
# TestPyPI carries none of the dependencies.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# The version the installed script reports, compared against the one asked of
# the index. Running it and discarding the output would pass while every
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
