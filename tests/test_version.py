"""The version is written once, and `--version` reads that one."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


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


def test_every_console_script_reports_it() -> None:
    """Driven, because `--version` goes through argparse rather than a return."""
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "version"
    ]
    for entry in ("gws", "gwp", "gwnb", "main"):
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                f"from worktrees.cli import {entry}; raise SystemExit({entry}())",
                "--version",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == declared, entry


def test_the_build_hook_writes_and_ships_the_version(tmp_path) -> None:
    """Both halves, because either one alone fails in silence.

    `.gitignore` keeps the generated file out of the repository, and hatchling
    reads `.gitignore`, so a hook that only writes it builds a wheel without
    it. Every install then falls back to the metadata lookup and nothing says
    so.
    """
    import hatch_build

    assert hatch_build.GENERATED.as_posix() in (ROOT / ".gitignore").read_text()

    (tmp_path / hatch_build.GENERATED.parent).mkdir(parents=True)
    build_data: dict[str, list[str]] = {}
    written = hatch_build.generate(tmp_path, "9.9.9", build_data)

    namespace: dict[str, str] = {}
    exec(written.read_text(), namespace)
    assert namespace["__version__"] == "9.9.9"
    assert f"/{hatch_build.GENERATED.as_posix()}" in build_data["artifacts"]


def test_a_checkout_falls_back_to_the_metadata_lookup() -> None:
    """No generated file is the normal state of a clone, and it still answers."""
    source = (ROOT / "src" / "worktrees" / "__init__.py").read_text()
    assert "from ._version import __version__" in source
    assert "except ImportError" in source
