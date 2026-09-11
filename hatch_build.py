"""Write the version `pyproject.toml` declares into the package it builds.

Reading it back through `importlib.metadata` at import instead costs 10 ms of
a 67 ms invocation, on every command, for a string two of them print. A
constant in the wheel costs nothing, and `pyproject.toml` stays the one place
the version is written.

Not `hatch-vcs`: deriving the version from a tag inverts which of the two has
to agree with the other, and `publish.yml` refuses a tag whose commit carries
a different version.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

GENERATED = Path("src") / "worktrees" / "_version.py"


def rendered(version: str) -> str:
    """The file's whole contents."""
    return (
        '"""Written at build time. See hatch_build.py."""\n'
        f'\n__version__ = "{version}"\n'
    )


def generate(root: Path, version: str, build_data: dict[str, Any]) -> Path:
    """Write the file, and tell the builder to ship it.

    Both halves, because either alone is silent. `.gitignore` keeps the
    generated file out of the repository and hatchling reads `.gitignore`, so
    without the artifacts entry the wheel ships without it and every install
    falls back to the metadata lookup with nothing to say so.
    """
    target = root / GENERATED
    target.write_text(rendered(version))
    build_data.setdefault("artifacts", []).append(f"/{GENERATED.as_posix()}")
    return target


class VersionHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        generate(Path(self.root), self.metadata.version, build_data)
