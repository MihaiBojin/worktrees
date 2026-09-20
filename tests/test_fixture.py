"""The fixture the driven tests run under.

A test that drives a console script hands it an environment, and what is in
that environment decides which git answers. That was written out five times
and wrong in all five.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import conftest

ROOT = Path(__file__).resolve().parents[1]


def test_the_suite_runs_against_this_machine_s_git() -> None:
    """`/usr/bin` first is Apple's stub on macOS.

    It re-execs the real binary, so every call pays twice, and after an Xcode
    update it answers every command with `exit 69` until somebody accepts a
    licence. On Linux that path is a real git, which is why CI never saw
    either and the suite could be wrong here for as long as it liked.
    """
    found = shutil.which("git")
    assert found, "no git on PATH"
    first = conftest.PATH.split(os.pathsep)[0]
    assert first == str(Path(found).parent), (
        f"the suite would run {first}/git rather than {found}"
    )


def test_no_module_writes_its_own_environment() -> None:
    """One `env_for`. There were five, and the PATH inside them had to be
    wrong five times before it was noticed once.
    """
    # Spelled in pieces so this file is not its own counter-example.
    stale = os.pathsep.join(("/usr/bin", "/bin", "/usr/local/bin", "/opt/homebrew/bin"))
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        body = path.read_text()
        assert stale not in body, f"{path.name} carries its own PATH"
        assert "def _env(world)" not in body, f"{path.name} builds its own env"


def test_the_environment_says_what_it_needs_and_no_more() -> None:
    """A list rather than `os.environ`, so a test inherits no more of the
    machine than it needs, and `GIT_CONFIG_*` so it reads none of its git
    config: on a machine with `commit.gpgsign=true` the first commit fails
    and every assertion after it is meaningless.
    """

    class FakeWorld:
        root = Path("/tmp/fixture-probe")

    env = conftest.env_for(FakeWorld())  # type: ignore[arg-type]
    assert set(env) == {
        "PATH",
        "PYTHONPATH",
        "HOME",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        *conftest.ENV,
    }
    assert env["HOME"] == str(FakeWorld.root)
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
