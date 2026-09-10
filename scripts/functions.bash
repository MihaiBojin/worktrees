#!/bin/bash
#
# Shared by the release scripts. Sourced, never run.

# Is the working copy carrying uncommitted changes?
is_dirty() {
    if [ -n "$(git status --porcelain)" ]; then
        echo "-dirty"
    else
        echo ""
    fi
}

# The release tags pointing at HEAD, 'v' prefix removed, one per line.
#
# --points-at rather than --contains: the latter lists every tag whose history
# includes HEAD, so checking out an older release returns that tag and every
# later one with it.
get_tags_at_head() {
    git tag --list 'v*' --points-at HEAD | sed 's/^v//'
}

# The project name from pyproject.toml.
# --no-project keeps this usable before the environment has been synced.
get_project_name() {
    dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
    uv run --no-project python -c \
        "import tomllib; print(tomllib.load(open('$dir/../pyproject.toml','rb'))['project']['name'])"
}

# The project version from pyproject.toml.
get_project_version() {
    dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
    uv run --no-project python -c \
        "import tomllib; print(tomllib.load(open('$dir/../pyproject.toml','rb'))['project']['version'])"
}
