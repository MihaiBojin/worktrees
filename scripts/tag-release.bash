#!/bin/bash
set -ueo pipefail

# Tags HEAD with the version in pyproject.toml, unless that tag already exists.
#
# Run on every push to main: a push that does not change the version finds its
# tag already there and does nothing, so only a merged version bump releases.
#
# The created tag goes to stdout, and nothing at all goes there when there was
# nothing to do, so a caller can branch on it. Commentary goes to stderr.
#
# --dry-run skips creating and pushing.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly DIR

# shellcheck disable=SC1091
source "$DIR/functions.bash"

DRY_RUN=""
while [[ "$#" -gt 0 ]]; do
    case $1 in
    --dry-run) DRY_RUN="yes" ;;
    *)
        echo "Error: unsupported argument $1" >&2
        exit 1
        ;;
    esac
    shift
done
readonly DRY_RUN

# The version is read from the working copy, so an uncommitted bump would tag a
# commit that does not carry it. CI checkouts are clean; this catches a local
# run.
if [ -n "$(is_dirty)" ]; then
    echo "Working directory is dirty, cannot proceed..." >&2
    git status --porcelain >&2
    exit 1
fi

VERSION="$(get_project_version)"
readonly VERSION
TAG="v$VERSION"
readonly TAG

# Ask the remote rather than the local clone, which in a CI checkout may carry
# no tags at all.
if [ -n "$(git ls-remote --tags origin "refs/tags/$TAG")" ]; then
    echo "$TAG is already on the remote; nothing to release." >&2
    exit 0
fi

if [ -n "$DRY_RUN" ]; then
    echo "Would tag $(git rev-parse --short HEAD) as $TAG" >&2
    echo "$TAG"
    exit 0
fi

git tag -a "$TAG" -m "$TAG"
git push origin "refs/tags/$TAG"
echo "Tagged $(git rev-parse --short HEAD) as $TAG" >&2
echo "$TAG"
