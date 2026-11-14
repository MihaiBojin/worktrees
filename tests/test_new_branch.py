"""gw branch create: fetch, then branch off the head branch or this checkout."""

from __future__ import annotations

import json
import os
import pty
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import argv, env_for, online

from worktrees import new_branch
from worktrees.git import Refused, guard

SRC = str(Path(__file__).resolve().parents[1] / "src")


def branch_create(world, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv("gw branch create", *args),
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )


def with_remote(world) -> None:
    """A remote whose HEAD is recorded, the way a clone records it."""
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    world.git("remote", "add", "origin", str(world.root / "upstream.git"))


def test_it_branches_off_the_head_branch(world) -> None:
    started = new_branch.create("feature")
    assert started.branch == "feature"
    assert started.base == "refs/heads/main"
    assert world.git("symbolic-ref", "--short", "HEAD") == "feature"


def test_the_base_is_the_remote_copy_when_there_is_one(world) -> None:
    """A branch made here is already on top of what the server has."""
    with_remote(world)
    world.commit("local-only", "x\n")  # main moves; origin/main does not
    started = new_branch.create("feature")
    assert started.base == "refs/remotes/origin/main"
    assert world.git("rev-parse", "HEAD") == world.git(
        "rev-parse", "refs/remotes/origin/main"
    )


def test_the_new_branch_does_not_track_the_head_branch(world) -> None:
    """A branch that tracked it would take it as its upstream, and git push
    would target the head branch."""
    with_remote(world)
    new_branch.create("feature")
    proc = subprocess.run(
        ["git", "-C", str(world.repo), "config", "--get", "branch.feature.merge"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, f"feature tracks {proc.stdout.strip()}"


def test_an_existing_branch_is_refused(world) -> None:
    world.git("branch", "taken", "main")
    with pytest.raises(new_branch.Refusal) as exc:
        new_branch.create("taken")
    assert "already a branch" in str(exc.value)
    assert "git switch taken checks it out" in str(exc.value)


@pytest.mark.parametrize("name", ["has space", "-leading", "a..b", "x.lock", ""])
def test_an_invalid_name_is_refused(world, name: str) -> None:
    with pytest.raises(new_branch.Refusal) as exc:
        new_branch.create(name)
    assert "not a valid branch name" in str(exc.value)
    assert world.git("symbolic-ref", "--short", "HEAD") == "main"


def test_a_tag_cannot_be_the_base(world) -> None:
    """The base travels as a full ref: a bare name resolves to a tag first."""
    with_remote(world)
    world.commit("later", "later\n")
    world.git("tag", "origin/main", "HEAD")  # the tag is not where origin/main is
    started = new_branch.create("feature")
    assert started.base == "refs/remotes/origin/main"
    assert world.git("rev-parse", "HEAD") != world.git(
        "rev-parse", "refs/tags/origin/main"
    )


def test_a_name_is_required(world) -> None:
    p = branch_create(world)
    assert p.returncode == 2
    assert p.stderr.startswith(
        "gw branch create needs NAME\n\nusage: gw branch create "
    )
    assert "the branch to start" in p.stderr
    assert p.stdout == ""


@pytest.mark.parametrize("flags", [(), ("-q",), ("--quiet",)])
def test_the_binary_reports_what_it_made(world, flags) -> None:
    p = branch_create(world, "feature", *flags)
    assert p.returncode == 0, p.stderr
    sha = world.git("rev-parse", "--short", "refs/heads/main")
    assert p.stdout.strip() == f"feature from main at {sha}"
    assert ("Head: main (inferred)" in p.stderr) is not flags


def test_json_is_data_on_stdout(world) -> None:
    p = branch_create(world, "feature", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["base"] == "refs/heads/main"


def test_a_refusal_leaves_the_checkout_alone(world) -> None:
    world.git("branch", "taken", "main")
    p = branch_create(world, "taken")
    assert p.returncode == 1
    assert "already a branch" in p.stderr
    assert p.stdout == ""
    assert world.git("symbolic-ref", "--short", "HEAD") == "main"


@pytest.mark.parametrize(("held", "wanted"), [("feat", "feat/x"), ("feat/x", "feat")])
def test_a_name_that_nests_with_a_branch_is_refused(world, held, wanted) -> None:
    world.git("branch", held, "main")
    p = branch_create(world, wanted)
    assert p.returncode == 1
    assert p.stderr.endswith(
        f"cannot create branch {wanted}: branch {held} exists\n"
        "git cannot have both a branch feat and branches whose names start with feat/\n"
    )
    assert world.git("symbolic-ref", "--short", "HEAD") == "main"


def test_a_forced_checkout_is_refused(world) -> None:
    """The same class as reset --hard: it discards the working tree."""
    for words in (
        ("checkout", "-f", "main"),
        ("checkout", "--force", "main"),
        ("switch", "--discard-changes", "main"),
    ):
        with pytest.raises(Refused):
            guard(words)
    guard(("checkout", "--no-track", "-b", "x", "refs/heads/main"))


def test_the_usage_line_names_what_was_typed(world) -> None:
    """`gw` and `worktrees` are two programs, and each names itself."""
    p = branch_create(world)
    assert p.returncode == 2
    assert p.stderr.startswith("gw branch create needs NAME\n")

    p = subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import main; raise SystemExit(main())",
            "branch",
            "create",
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert p.returncode == 2
    assert p.stderr.startswith("worktrees branch create needs NAME\n")
    assert "usage: worktrees branch create " in p.stderr


def on_a_terminal(world, *args: str, answer: str, env=None) -> tuple[int, str]:
    """A real tty on stdin, so the question is reachable."""
    parent, child = pty.openpty()
    proc = subprocess.Popen(
        argv("gw branch create", *args),
        cwd=str(world.repo),
        stdin=child,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env or env_for(world),
    )
    os.close(child)
    os.write(parent, answer.encode())
    out = proc.stdout.read() if proc.stdout else ""
    proc.wait()
    os.close(parent)
    return proc.returncode, out


def disk_remote(world) -> None:
    """An origin on this disk, so a sync fetches for real and fast."""
    bare = world.root / "upstream.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    world.git("remote", "add", "origin", str(bare))
    world.git("push", "-q", "origin", "main")
    world.git("fetch", "-q", "origin")
    world.git("remote", "set-head", "origin", "main")


def on_feature(world) -> str:
    world.git("switch", "-q", "-c", "feature")
    world.commit("feature work", "f\n")
    return world.git("rev-parse", "HEAD")


QUESTION = "stack next on feature, not main? [y/N]"


def test_a_terminal_on_another_branch_can_stack_there(world) -> None:
    tip = on_feature(world)
    code, out = on_a_terminal(world, "next", answer="y\n")
    assert code == 0, out
    assert QUESTION in out
    assert "next from feature at" in out
    assert world.git("rev-parse", "HEAD") == tip


def test_no_answer_starts_from_the_head_ref(world) -> None:
    with_remote(world)
    on_feature(world)
    code, out = on_a_terminal(world, "next", answer="\n")
    assert code == 0, out
    assert QUESTION in out
    assert world.git("rev-parse", "HEAD") == world.git(
        "rev-parse", "refs/remotes/origin/main"
    )


def test_the_head_branch_asks_nothing(world) -> None:
    code, out = on_a_terminal(world, "next", answer="y\n")
    assert code == 0, out
    assert "stack" not in out


def test_without_a_terminal_nothing_is_asked(world) -> None:
    with_remote(world)
    on_feature(world)
    p = branch_create(world, "next")
    assert p.returncode == 0, p.stderr
    assert "stack" not in p.stderr
    assert world.git("rev-parse", "HEAD") == world.git(
        "rev-parse", "refs/remotes/origin/main"
    )


def test_a_refused_name_is_refused_before_the_question(world) -> None:
    on_feature(world)
    world.git("branch", "taken", "main")
    code, out = on_a_terminal(world, "taken", answer="y\n")
    assert code == 1
    assert "already a branch" in out
    assert "stack" not in out


def test_here_starts_from_the_local_head_branch(world) -> None:
    """On the head branch, --here takes its last local commit, not the
    remote's."""
    with_remote(world)
    world.commit("local-only", "x\n")
    p = branch_create(world, "next", "--here", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["base"] == "refs/heads/main"
    assert world.git("rev-parse", "HEAD") == world.git("rev-parse", "refs/heads/main")


def test_here_stacks_on_another_branch(world) -> None:
    tip = on_feature(world)
    p = branch_create(world, "next", "--here", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["base"] == "refs/heads/feature"
    assert world.git("rev-parse", "HEAD") == tip


def test_here_does_not_sync(world) -> None:
    disk_remote(world)
    on_feature(world)

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv("gw branch create", *args, "-v"),
            cwd=str(world.repo),
            capture_output=True,
            text=True,
            env=online(world),
        )

    p = run("stacked", "--here")
    assert p.returncode == 0, p.stderr
    assert "fetch" not in p.stderr
    world.git("switch", "-q", "feature")
    p = run("fresh")
    assert p.returncode == 0, p.stderr
    assert "fetch" in p.stderr


def test_the_question_waits_for_the_sync_it_runs_beside(world) -> None:
    disk_remote(world)
    on_feature(world)
    code, out = on_a_terminal(world, "next", answer="\n", env=online(world))
    assert code == 0, out
    assert QUESTION in out
    assert world.git("rev-parse", "HEAD") == world.git(
        "rev-parse", "refs/remotes/origin/main"
    )
