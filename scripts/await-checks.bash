#!/bin/bash
set -ueo pipefail

# Wait for the test workflow on a commit, so a release does not start against
# a tree that is already known to be broken.
#
# Asks for that workflow's runs by name rather than for the commit's check
# runs. The publish workflow writes check runs onto the same commit, so
# "wait until every check run is complete" would be waiting for itself.
#
# $1 is the sha. GH_TOKEN and GITHUB_REPOSITORY come from the workflow.

readonly SHA="${1:?usage: await-checks.bash <sha>}"
readonly WORKFLOW="tests.yml"
readonly DEADLINE=$((SECONDS + 1800))

while true; do
    STATE="$(gh run list --commit "$SHA" --workflow "$WORKFLOW" \
        --json status,conclusion --jq '.[0] | "\(.status) \(.conclusion // "-")"')"

    case "$STATE" in
    "completed success")
        echo "$WORKFLOW passed on $SHA"
        exit 0
        ;;
    "completed "*)
        echo "$WORKFLOW on $SHA: ${STATE#completed }. Refusing to release it." >&2
        exit 1
        ;;
    "")
        # No run at all yet. A commit that reached main has one; a tag pushed
        # seconds after the merge may be ahead of it.
        echo "waiting for $WORKFLOW to start on $SHA..." >&2
        ;;
    *)
        echo "waiting for $WORKFLOW on $SHA: $STATE" >&2
        ;;
    esac

    if [ "$SECONDS" -ge "$DEADLINE" ]; then
        echo "Gave up waiting for $WORKFLOW on $SHA after 30 minutes." >&2
        exit 1
    fi
    sleep 15
done
