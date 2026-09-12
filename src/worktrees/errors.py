"""What went wrong, for a caller that has not loaded the machinery.

Its own module because `cli` catches both in `_dispatch`, which runs for
every command including the ones that never reach git. Raising them lives in
`git.py`; naming them costs nothing and pulls nothing.
"""

from __future__ import annotations


class GitError(RuntimeError):
    """git exited with a code the caller did not declare acceptable."""


class Refused(Exception):
    """The guard will not issue this command. No flag reaches past it."""
