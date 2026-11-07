"""`pyproject.toml` declares the version and `gw version` reads that one. The
shell plugins and the README's install lines carry copies, which
`scripts/bump_version.py` writes and these hold to it."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import bump_version  # noqa: E402

from worktrees import cli  # noqa: E402


def _declared() -> str:
    return str(
        tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    )


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


# --------------------------------------------------------------------------
# the copies the shell plugins carry
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "template"),
    [(n, t) for n, ts in bump_version.STAMPS.items() for t in ts],
)
def test_every_copy_names_the_declared_release(name: str, template: str) -> None:
    """A release pull request whose bump missed a copy fails here, before
    anything is tagged."""
    assert template.format(_declared()) in (ROOT / name).read_text()


def _copy(tmp_path: Path) -> Path:
    """The script and the files it stamps, laid out as the repository has them."""
    for name in [*bump_version.STAMPS, "scripts/bump_version.py"]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, tmp_path / name)
    return tmp_path


def _bump(root: Path, version: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the copied script with a `uv` that records what it was asked."""
    seen = root / "uv-argv"
    fake = root / "bin" / "uv"
    fake.parent.mkdir()
    fake.write_text(f'#!/bin/sh\necho "$PWD $*" > "{seen}"\n')
    fake.chmod(0o755)
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "bump_version.py"), version],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}"},
    )
    return proc, seen


def test_the_bump_writes_every_copy_and_runs_uv_version(tmp_path) -> None:
    root = _copy(tmp_path)
    proc, seen = _bump(root, "9.9.9")
    assert proc.returncode == 0, proc.stderr
    assert seen.read_text().split() == [str(root.resolve()), "version", "9.9.9"]
    for name, templates in bump_version.STAMPS.items():
        text = (root / name).read_text()
        for template in templates:
            assert template.format("9.9.9") in text, (name, template)
            assert template.format(_declared()) not in text, (name, template)


def test_a_line_the_bump_cannot_find_changes_nothing(tmp_path) -> None:
    """Checked before anything is written, so a renamed variable stops the
    release with every file as it was rather than half of them bumped."""
    root = _copy(tmp_path)
    zsh = root / "zsh/plugins/worktrees/worktrees.plugin.zsh"
    zsh.write_text(zsh.read_text().replace("WORKTREES_PLUGIN_VERSION", "RENAMED"))
    before = {name: (root / name).read_text() for name in bump_version.STAMPS}

    proc, seen = _bump(root, "9.9.9")

    assert proc.returncode != 0
    assert "export WORKTREES_PLUGIN_VERSION" in proc.stderr
    assert not seen.exists()
    assert {name: (root / name).read_text() for name in bump_version.STAMPS} == before


# --------------------------------------------------------------------------
# what root help and gw version say about the plugin
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plugin", "same", "says"),
    [
        ("0.6.0", True, "the same release as these commands"),
        ("0.5.1", False, "older than these commands.\nPin it to v0.6.0"),
        ("0.10.0", False, "newer than these commands.\nUpgrade them"),
        ("0.6.0.post1", False, "and these commands are 0.6.0"),
        ("", False, "No shell plugin here says which release it is"),
    ],
)
def test_the_plugin_is_compared_with_these_commands(
    monkeypatch, plugin: str, same: bool, says: str
) -> None:
    """0.10.0 is newer than 0.6.0, which a string comparison gets backwards.
    Anything that is not `x.y.z` is printed beside ours and not ranked."""
    monkeypatch.setattr(cli, "__version__", "0.6.0")
    monkeypatch.setenv(cli.PLUGIN_VERSION, plugin)
    text, matched = cli.plugin_state()
    assert matched is same
    assert says in text


def _gw(*args: str, plugin: str | None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != cli.PLUGIN_VERSION}
    if plugin is not None:
        env[cli.PLUGIN_VERSION] = plugin
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gw; raise SystemExit(gw())",
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.parametrize("plugin", [None, "0.0.1", "99.0.0", _declared()])
def test_gw_version_keeps_stdout_to_the_version(plugin: str | None) -> None:
    """`scripts/verify-publish.bash` compares stdout with the version it asked
    the index for, so what the plugin says goes to stderr."""
    proc = _gw("version", plugin=plugin)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == f"{_declared()}\n"
    assert "shell plugin" in proc.stderr


@pytest.mark.parametrize("plugin", [None, "0.0.1", "99.0.0", "0.6.0.post1789247568"])
def test_root_help_says_what_the_plugin_is_inside_eighty_columns(
    plugin: str | None,
) -> None:
    proc = _gw("--help", plugin=plugin)
    assert proc.returncode == 0, proc.stderr
    assert "shell plugin" in proc.stdout
    assert [ln for ln in proc.stdout.splitlines() if len(ln) > 80] == []
