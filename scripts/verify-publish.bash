#!/bin/bash
set -ueo pipefail

# Install the published package from an index and run what it installed, so a
# green publish means installable rather than uploaded.
#
# --prod reads PyPI; without it, TestPyPI.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly DIR

# shellcheck disable=SC1091
source "$DIR/functions.bash"

INDEX="https://test.pypi.org/simple/"
WHICH="TestPyPI"
if [ "${1:-}" = "--prod" ]; then
    INDEX="https://pypi.org/simple/"
    WHICH="PyPI"
fi
readonly INDEX WHICH

NAME="$(get_project_name)"
VERSION="$(get_project_version)"
readonly NAME VERSION

echo "Verifying $NAME==$VERSION from $WHICH..." >&2

# --isolated and a throwaway cache, so a local build of the same version
# cannot answer for the index. --index-strategy unsafe-best-match because
# TestPyPI carries none of the dependencies.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

UV_CACHE_DIR="$TMP/cache" uv tool run \
    --isolated \
    --index "$INDEX" \
    --index-strategy unsafe-best-match \
    --from "$NAME==$VERSION" \
    gws --version

echo "$NAME==$VERSION installs from $WHICH and runs." >&2
