"""The version is written once, and `gw version` reads that one."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_package_declares_its_version_nowhere() -> None:
    """A second copy is a second thing to bump, and `uv version` bumps one.

    `uv version 0.2.0` rewrites pyproject.toml and uv.lock. It does not touch
    Python source, so a literal here would survive a release and every
    console script would report the version before it.
    """
    source = (ROOT / "src" / "worktrees" / "__init__.py").read_text()
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "version"
    ]
    assert declared not in source, (
        f"{declared} is written into __init__.py as well as pyproject.toml"
    )
    assert 'version("git-worktrees")' in source


def test_version_matches_the_distribution() -> None:
    from worktrees import __version__

    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "version"
    ]
    assert __version__ == declared


def test_both_names_that_take_a_subcommand_report_it() -> None:
    """Driven, because the console script is what a person runs."""
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "version"
    ]
    for entry in ("gw", "main"):
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
                "version",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == declared, entry
