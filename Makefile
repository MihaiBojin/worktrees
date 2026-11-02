.PHONY: local-install reset

# The working copy, in place of anything published.
local-install:
	uv tool install --force --from . git-worktrees

# Undoes local-install: the latest release from PyPI, and only its commands.
reset:
	uv tool install --force git-worktrees
