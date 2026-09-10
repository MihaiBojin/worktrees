#!/bin/bash
set -ueo pipefail

# Everything that has to be true before a release starts, checked while every
# one of them is still free to fix.
#
# Run from a clean checkout with origin fetched. $1 is the version, without
# the 'v'.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly DIR

# shellcheck disable=SC1091
source "$DIR/functions.bash"

readonly WANT="${1:?usage: check-releasable.bash <version>}"
readonly TAG="v$WANT"

fail() {
    echo "$*" >&2
    exit 1
}

# 1. Shape. The publish workflow triggers on v*.*.*, so a version it cannot
#    match would merge and then release nothing at all.
[[ "$WANT" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
    fail "$WANT is not x.y.z, and publish.yml only triggers on v*.*.*"

# 2. A clean tree, because the release branch is cut from origin/main and
#    `git switch --create` would carry uncommitted work onto it.
[ -z "$(git status --porcelain)" ] || {
    git status --porcelain >&2
    fail "Working directory is dirty; commit or stash first."
}

# 3. Forward only. Comparing as integers rather than as strings, so 0.10.0
#    is after 0.9.0.
# 0.0.0 when main has no pyproject.toml yet, which is true of a repository
# that has never released.
if git cat-file -e "origin/main:pyproject.toml" 2>/dev/null; then
    HAVE="$(git show origin/main:pyproject.toml |
        uv run --no-project python -c \
            'import sys,tomllib; print(tomllib.load(sys.stdin.buffer)["project"]["version"])')"
else
    HAVE="0.0.0"
fi
readonly HAVE
uv run --no-project python -c '
import sys
have, want = (tuple(int(p) for p in v.split(".")) for v in sys.argv[1:3])
sys.exit(0 if want > have else 1)
' "$HAVE" "$WANT" || fail "origin/main is already at $HAVE; $WANT is not after it."

# 4. The tag has to be free. The remote is what matters: a local clone may
#    carry no tags at all.
[ -z "$(git ls-remote --tags origin "refs/tags/$TAG")" ] ||
    fail "$TAG already exists on the remote. Releases do not move tags; pick the next version."

# 5. And the index, because PyPI refuses a duplicate upload and does so at
#    the very end of the run, after everything else has already happened.
NAME="$(get_project_name)"
readonly NAME
CODE="$(curl -fsS -o /dev/null -w '%{http_code}' "https://pypi.org/pypi/$NAME/$WANT/json" || true)"
readonly CODE
[ "$CODE" = "404" ] ||
    fail "PyPI already has $NAME $WANT (HTTP $CODE). A version cannot be replaced."

echo "$NAME $HAVE -> $WANT, and $TAG is free on the remote and on PyPI."
