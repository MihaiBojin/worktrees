.PHONY: local-install install

# The working copy, in place of anything published.
local-install:
	uv tool install --force --from . git-worktrees

# The latest release on PyPI.
install:
	uv tool install --force git-worktrees
