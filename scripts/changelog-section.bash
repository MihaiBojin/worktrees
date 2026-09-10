#!/bin/bash
set -ueo pipefail

# Print one version's section of CHANGELOG.md, heading excluded, so the
# GitHub release carries what was written rather than a list of pull request
# titles.
#
# $1 is the version without the 'v'.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
readonly DIR
readonly FILE="$DIR/../CHANGELOG.md"
readonly WANT="${1:?usage: changelog-section.bash <version>}"

[ -f "$FILE" ] || {
    echo "No CHANGELOG.md at $FILE" >&2
    exit 1
}

# From the wanted heading to the next '## ', exclusive at both ends. awk
# rather than sed, so the heading match is anchored on the whole line and a
# version that is a prefix of another cannot open the wrong section.
SECTION="$(awk -v want="$WANT" '
    /^## / {
        if (inside) { exit }
        # "## 0.2.0" or "## 0.2.0 - 2026-09-11"
        heading = substr($0, 4)
        sub(/ +-.*$/, "", heading)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", heading)
        if (heading == want) { inside = 1 }
        next
    }
    inside { print }
' "$FILE")"
readonly SECTION

# Strip the blank lines the boundaries leave behind.
TRIMMED="$(printf '%s\n' "$SECTION" | sed -e '/./,$!d' | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}')"
readonly TRIMMED

[ -n "$TRIMMED" ] || {
    echo "CHANGELOG.md has no section for $WANT." >&2
    echo "Write one before releasing; /release does this for you." >&2
    exit 1
}

printf '%s\n' "$TRIMMED"
