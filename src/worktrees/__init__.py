"""Git worktree commands that refuse to lose work."""

try:
    # Written into the wheel by hatch_build.py, from the version
    # pyproject.toml declares. Asking importlib.metadata for it instead costs
    # 10 ms of a 67 ms invocation, on every command, for a string two of them
    # print.
    from ._version import __version__
except ImportError:  # a checkout, or an editable install of one
    from importlib.metadata import PackageNotFoundError, version

    try:
        __version__ = version("git-worktrees")
    except PackageNotFoundError:  # a source tree nothing has installed
        __version__ = "0+unknown"
