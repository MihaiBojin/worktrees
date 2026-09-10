#!/bin/bash
set -ueo pipefail

# Refuse a tag that is not on the default branch.
#
# Releasing from a commit main never took means publishing something no
# review, no merge and no branch protection ever saw. --is-ancestor rather
# than an equality check, so deliberately releasing an older commit on main
# still works.

readonly BRANCH="${1:-main}"

git fetch --quiet origin "$BRANCH"

if ! git merge-base --is-ancestor HEAD "refs/remotes/origin/$BRANCH"; then
    echo "This commit is not on origin/$BRANCH, refusing to publish it." >&2
    echo "  commit: $(git rev-parse HEAD)" >&2
    echo "  tag(s): $(git tag --list 'v*' --points-at HEAD | tr '\n' ' ')" >&2
    echo >&2
    echo "Merge it first, then tag the commit on $BRANCH." >&2
    exit 1
fi

echo "$(git rev-parse --short HEAD) is on origin/$BRANCH"
