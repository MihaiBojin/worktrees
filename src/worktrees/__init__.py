"""Git worktree commands that refuse to lose work."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("git-worktrees")
except PackageNotFoundError:  # a source tree nothing has installed
    __version__ = "0+unknown"
