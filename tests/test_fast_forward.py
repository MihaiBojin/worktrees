"""fast-forward: the branch you stand on, level with the head ref."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime

import pytest
from conftest import argv, env_for

from worktrees import fast_forward as F
from worktrees.new_branch import Refusal

STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z/")
NOW = datetime(2026, 11, 8, 9, 5, 3, tzinfo=UTC)


def gw_ff(world, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv("gw fast-forward", *args),
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env={**env_for(world), **env},
    )


def with_work(world, branch: str = "fix-parser") -> str:
    """A branch off main carrying one commit of its own."""
    world.git("switch", "--quiet", "--create", branch)
    return world.commit(branch.replace("/", "-") + ".txt", "w\n")


def advance(world, name: str = "main.txt", body: str = "m\n") -> str:
    """One more commit on main, leaving HEAD where it was."""
    here = world.git("symbolic-ref", "--short", "HEAD")
    world.git("switch", "--quiet", "main")
    sha = world.commit(name, body)
    world.git("switch", "--quiet", here)
    return sha


def squashed(world, branch: str = "fix-parser") -> str:
    """A branch whose one commit main now carries as a squash."""
    tip = with_work(world, branch)
    world.git("switch", "--quiet", "main")
    world.git("merge", "--quiet", "--squash", branch)
    world.git("commit", "--quiet", "-m", f"{branch} (#1)")
    world.git("switch", "--quiet", branch)
    return tip


def with_remote(world) -> None:
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    world.git("remote", "add", "origin", str(world.root / "upstream.git"))


def rebasing(world) -> bool:
    return (world.repo / world.git("rev-parse", "--git-path", "rebase-merge")).is_dir()


def conflicting(world) -> str:
    """fix-parser and main each rewrite the same line; the branch's sha."""
    world.git("switch", "--quiet", "--create", "fix-parser")
    tip = world.commit("README", "branch\n")
    advance(world, "README", "main\n")
    return tip


# --------------------------------------------------------------------------
# nothing to do
# --------------------------------------------------------------------------


def test_a_branch_at_the_head_does_nothing(world) -> None:
    world.git("switch", "--quiet", "--create", "fix-parser")
    before = world.git("rev-parse", "HEAD")

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["result"] == "current"
    assert payload["branch"] == "fix-parser"
    assert payload["ahead"] == 0
    assert world.git("rev-parse", "HEAD") == before
    assert world.git("symbolic-ref", "--short", "HEAD") == "fix-parser"
    assert world.git("branch", "--format=%(refname:short)").split() == [
        "fix-parser",
        "main",
    ]


def test_a_branch_with_commits_on_the_head_does_nothing(world) -> None:
    tip = with_work(world)
    moved = F.fast_forward()
    assert moved.result == F.CURRENT
    assert moved.ahead == 1
    assert world.git("rev-parse", "HEAD") == tip


# --------------------------------------------------------------------------
# a branch behind the head
# --------------------------------------------------------------------------


def test_a_branch_behind_the_head_is_rebased_onto_it(world) -> None:
    with_work(world)
    head = advance(world)

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["result"] == "rebased"
    assert payload["branch"] == "fix-parser"
    assert (payload["ahead"], payload["behind"]) == (1, 1)
    assert world.git("rev-parse", "HEAD^") == head
    assert world.git("symbolic-ref", "--short", "HEAD") == "fix-parser"


def test_a_branch_with_nothing_of_its_own_is_fast_forwarded(world) -> None:
    world.git("switch", "--quiet", "--create", "fix-parser")
    head = advance(world)

    moved = F.fast_forward()
    assert moved.result == F.FAST_FORWARDED
    assert moved.behind == 1
    assert world.git("rev-parse", "HEAD") == head


def test_a_conflict_stops_the_rebase_and_names_the_commit(world) -> None:
    tip = conflicting(world)
    short = world.git("rev-parse", "--short", tip)

    p = gw_ff(world, "--json")
    assert p.returncode == 1
    payload = json.loads(p.stdout)
    assert payload["result"] == "conflict"
    assert payload["branch"] == "fix-parser"
    assert payload["stopped_at"] == short
    assert payload["subject"] == "README"
    assert payload["paths"] == ["README"]
    assert payload["previous_sha"] == short
    # Left in place for somebody to resolve, not aborted.
    assert rebasing(world)
    assert "<<<<<<<" in (world.repo / "README").read_text()
    assert short in p.stderr
    assert "git rebase --abort" in p.stderr
    assert "git rebase --continue" in p.stderr


def test_a_stopped_rebase_is_reported_until_it_finishes(world) -> None:
    """So whoever resolves it can run the command again to see where it is."""
    conflicting(world)
    assert F.fast_forward().result == F.CONFLICT

    again = F.fast_forward()
    assert again.result == F.CONFLICT
    assert again.paths == ["README"]

    (world.repo / "README").write_text("both\n")
    world.git("add", "README")
    staged = F.fast_forward()
    assert staged.result == F.CONFLICT
    assert staged.paths == []

    subprocess.run(
        ["git", "rebase", "--continue"],
        cwd=world.repo,
        env={**os.environ, "GIT_EDITOR": "true"},
        check=True,
        capture_output=True,
    )
    done = F.fast_forward()
    assert done.result == F.CURRENT
    assert done.ahead == 1


def dirty_and_behind(world) -> None:
    """Uncommitted work on a file main has since changed, under autoStash."""
    with_work(world)
    advance(world, "README", "main\n")
    world.git("config", "rebase.autoStash", "true")
    (world.repo / "README").write_text("uncommitted\n")


def test_raw_git_with_autostash_leaves_the_work_in_the_shared_stash(world) -> None:
    """The behaviour the refusal below exists for: the work goes to the stash
    stack every worktree shares, and comes back as conflict markers."""
    dirty_and_behind(world)
    subprocess.run(["git", "rebase", "main"], cwd=world.repo, capture_output=True)
    assert world.git("stash", "list")
    assert "<<<<<<<" in (world.repo / "README").read_text()


def test_a_dirty_tree_is_refused_whatever_autostash_says(world) -> None:
    dirty_and_behind(world)
    before = world.git("rev-parse", "HEAD")

    p = gw_ff(world)
    assert p.returncode == 1
    assert p.stdout == ""
    assert "unstaged changes" in p.stderr
    assert world.git("stash", "list") == ""
    assert world.git("rev-parse", "HEAD") == before
    assert (world.repo / "README").read_text() == "uncommitted\n"
    assert not rebasing(world)


def dirty_and_level_behind(world) -> None:
    """No commits of its own, and uncommitted work on a file main has since
    changed, under merge.autoStash."""
    world.git("switch", "--quiet", "--create", "fix-parser")
    advance(world, "README", "main\n")
    world.git("config", "merge.autoStash", "true")
    (world.repo / "README").write_text("uncommitted\n")


def test_raw_git_merge_with_autostash_leaves_the_work_in_the_shared_stash(
    world,
) -> None:
    """The same hazard on the fast-forward path, at exit 0."""
    dirty_and_level_behind(world)
    raw = subprocess.run(
        ["git", "merge", "--ff-only", "main"], cwd=world.repo, capture_output=True
    )
    assert raw.returncode == 0
    assert world.git("stash", "list")
    assert "<<<<<<<" in (world.repo / "README").read_text()


def test_a_fast_forward_over_uncommitted_work_is_refused(world) -> None:
    dirty_and_level_behind(world)
    before = world.git("rev-parse", "HEAD")

    p = gw_ff(world)
    assert p.returncode == 1
    assert p.stdout == ""
    assert "would be overwritten" in p.stderr
    assert world.git("stash", "list") == ""
    assert world.git("rev-parse", "HEAD") == before
    assert (world.repo / "README").read_text() == "uncommitted\n"


# --------------------------------------------------------------------------
# a finished branch
# --------------------------------------------------------------------------


def test_a_squash_merged_branch_moves_to_a_new_branch(world) -> None:
    tip = squashed(world)
    head = world.git("rev-parse", "main")

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["result"] == "rotated"
    assert payload["previous"] == "fix-parser"
    assert payload["proof"] == "squash"
    assert payload["base"] == "refs/heads/main"
    assert STAMP.match(payload["branch"])
    assert payload["branch"].endswith("Z/fix-parser")
    assert world.git("symbolic-ref", "--short", "HEAD") == payload["branch"]
    assert world.git("rev-parse", "HEAD") == head
    # The finished branch is left exactly where it was.
    assert world.git("rev-parse", "refs/heads/fix-parser") == tip


def test_a_branch_merged_with_a_merge_commit_moves_too(world) -> None:
    path = world.merged_worktree("fix-parser")
    moved = F.fast_forward(repo=path, now=NOW)
    assert moved.result == F.ROTATED
    assert moved.proof == "ancestry"
    assert moved.branch == "2026-11-08T09-05-03Z/fix-parser"


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("fix-parser", "fix-parser"),
        ("2026-11-07T23-59-59Z/fix-parser", "fix-parser"),
        ("2026-11-07T23-59-59Z/feature/oauth", "feature/oauth"),
        # Not a prefix this wrote.
        ("2026-11-07/fix-parser", "2026-11-07/fix-parser"),
        ("fix/2026-11-07T23-59-59Z/x", "fix/2026-11-07T23-59-59Z/x"),
        ("fix-parser-2026-09-10_001", "fix-parser-2026-09-10_001"),
    ],
)
def test_the_stem_drops_only_a_prefix_it_wrote(branch: str, expected: str) -> None:
    assert F.stem(branch) == expected


def test_rotating_a_rotated_branch_replaces_the_time(world) -> None:
    squashed(world, "2026-11-07T23-59-59Z/fix-parser")
    moved = F.fast_forward(now=NOW)
    assert moved.branch == "2026-11-08T09-05-03Z/fix-parser"


def test_a_name_is_used_as_given(world) -> None:
    squashed(world)
    p = gw_ff(world, "next-thing", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["branch"] == "next-thing"
    assert world.git("symbolic-ref", "--short", "HEAD") == "next-thing"


@pytest.mark.parametrize("kind", ["branch", "sha"])
def test_from_starts_the_new_branch_elsewhere(world, kind: str) -> None:
    squashed(world)
    world.git("switch", "--quiet", "--create", "release", "main~1")
    elsewhere = world.commit("release.txt")
    world.git("switch", "--quiet", "fix-parser")
    start = "release" if kind == "branch" else elsewhere[:10]

    moved = F.fast_forward("next-thing", start)
    assert moved.result == F.ROTATED
    assert world.git("rev-parse", "HEAD") == elsewhere
    assert moved.base == (
        "refs/heads/release"
        if kind == "branch"
        else world.git("rev-parse", "--short", elsewhere)
    )


def test_from_prefers_a_branch_to_a_tag_of_its_name(world) -> None:
    squashed(world)
    world.git("branch", "release", "main~1")
    world.git("tag", "release", "main")
    moved = F.fast_forward(start="release", now=NOW)
    assert moved.base == "refs/heads/release"
    assert world.git("rev-parse", "HEAD") == world.git("rev-parse", "main~1")


@pytest.mark.parametrize("start", ["no-such-thing", "--orphan", "-b"])
def test_from_that_names_nothing_is_refused(world, start: str) -> None:
    squashed(world)
    p = gw_ff(world, f"--from={start}")
    assert p.returncode == 1
    assert "names no branch or commit" in p.stderr
    assert world.git("symbolic-ref", "--short", "HEAD") == "fix-parser"


def test_a_name_another_branch_holds_is_refused(world) -> None:
    squashed(world)
    world.git("branch", "taken", "main")
    with pytest.raises(Refusal, match="taken is already a branch"):
        F.fast_forward("taken")
    assert world.git("symbolic-ref", "--short", "HEAD") == "fix-parser"


def test_a_name_only_the_remote_holds_is_refused(world) -> None:
    """Free here and taken upstream is not free; pushing would land on it."""
    with_remote(world)
    squashed(world)
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("update-ref", "refs/remotes/origin/taken", "main")
    with pytest.raises(Refusal, match="origin/taken already exists"):
        F.fast_forward("taken")


@pytest.mark.parametrize("args", [("next-thing",), ("--from", "main")])
def test_naming_an_unfinished_branch_is_refused(world, args) -> None:
    """A name only says where a finished branch goes, so on any other one it
    is a mistake rather than a request to rebase."""
    with_work(world)
    advance(world)
    before = world.git("rev-parse", "HEAD")
    p = gw_ff(world, *args)
    assert p.returncode == 1
    assert "only name the branch a finished one moves to" in p.stderr
    assert world.git("rev-parse", "HEAD") == before
    assert not world.git("branch", "--list", "next-thing")


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------


def test_a_detached_head_is_refused(world) -> None:
    world.git("checkout", "--quiet", "--detach", "HEAD")
    with pytest.raises(Refusal, match="HEAD is detached"):
        F.fast_forward()


def test_a_branch_with_no_common_history_is_left_alone(world) -> None:
    world.git("switch", "--quiet", "--orphan", "elsewhere")
    tip = world.commit("other.txt")
    p = gw_ff(world)
    assert p.returncode == 1
    assert "no common history" in p.stderr
    assert world.git("rev-parse", "HEAD") == tip


def test_a_tag_cannot_become_the_base(world) -> None:
    with_remote(world)
    with_work(world)
    world.git("tag", "origin/main", "refs/heads/fix-parser")

    moved = F.fast_forward()
    assert moved.result == F.CURRENT
    assert moved.base == "refs/remotes/origin/main"


# --------------------------------------------------------------------------
# the head branch
# --------------------------------------------------------------------------


def test_the_head_branch_only_ever_fast_forwards(world) -> None:
    """Rewriting local commits on the head branch is the class this tooling
    refuses everywhere else."""
    from worktrees.git import options

    with_remote(world)
    world.git("switch", "--quiet", "--create", "ahead", "main")
    moved_to = world.commit("remote.txt", "r\n")
    world.git("update-ref", "refs/remotes/origin/main", moved_to)
    world.git("switch", "--quiet", "main")
    before = world.git("rev-parse", "--short", "refs/heads/main")
    options.log.clear()

    moved = F.fast_forward()
    assert moved.result == F.FAST_FORWARDED
    assert (moved.branch, moved.previous_sha, moved.behind) == ("main", before, 1)
    assert moved.base == "refs/remotes/origin/main"
    assert world.git("rev-parse", "refs/heads/main") == moved_to
    assert any("--ff-only" in argv for argv in options.log)
    assert not any("rebase" in argv for argv in options.log)


def test_a_head_branch_already_level_does_nothing(world) -> None:
    with_remote(world)
    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["result"] == "current"


def test_a_head_branch_ahead_of_the_remote_is_refused(world) -> None:
    """Those commits are a change of their own, and --ff-only says so."""
    with_remote(world)
    world.commit("local.txt", "x\n")
    before = world.git("rev-parse", "HEAD")

    with pytest.raises(Refusal) as exc:
        F.fast_forward()
    assert "1 commit(s) ahead of origin/main" in str(exc.value)
    assert world.git("rev-parse", "HEAD") == before


def test_naming_the_head_branch_is_refused(world) -> None:
    with pytest.raises(Refusal, match="main is the head branch"):
        F.fast_forward("next-thing")


# --------------------------------------------------------------------------
# the binary
# --------------------------------------------------------------------------


@pytest.mark.parametrize("flags", [(), ("-q",), ("--quiet",)])
def test_the_binary_reports_where_it_was_and_where_it_is(world, flags) -> None:
    with_work(world)
    was = world.git("rev-parse", "--short", "HEAD")
    advance(world)

    p = gw_ff(world, *flags)
    assert p.returncode == 0, p.stderr
    now = world.git("rev-parse", "--short", "HEAD")
    first, second = p.stdout.splitlines()
    assert first.split()[:3] == ["was", "fix-parser", was]
    assert first.endswith("1 commit behind main")
    assert second.split()[:3] == ["now", "fix-parser", now]
    assert second.endswith("rebased onto main, 1 commit of its own")


def test_a_rotation_reports_both_branches(world) -> None:
    squashed(world)
    was = world.git("rev-parse", "--short", "HEAD")
    p = gw_ff(world)
    assert p.returncode == 0, p.stderr
    first, second = p.stdout.splitlines()
    assert first.split()[:3] == ["was", "fix-parser", was]
    assert "kept, finished: its tree or combined patch" in first
    assert STAMP.match(second.split()[1])
    assert second.endswith("new, from main")


def test_nothing_to_do_is_one_line(world) -> None:
    with_work(world)
    p = gw_ff(world)
    assert p.returncode == 0, p.stderr
    (line,) = p.stdout.splitlines()
    assert line.split()[:2] == ["now", "fix-parser"]
    assert line.endswith("already on main, 1 commit of its own")


def test_a_refusal_prints_no_data(world) -> None:
    world.git("checkout", "--quiet", "--detach", "HEAD")
    p = gw_ff(world, "--json")
    assert p.returncode == 1
    assert p.stdout == ""
    assert "HEAD is detached" in p.stderr


def test_the_shorthand_is_the_same_command(world) -> None:
    with_work(world)
    p = subprocess.run(
        argv("gw ff", "--json"),
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["result"] == "current"


def test_fast_forward_rejects_retired_refresh_alias(world) -> None:
    before = world.git("rev-parse", "HEAD")
    p = gw_ff(world, "-f", "-v")
    assert p.returncode == 2
    assert world.git("rev-parse", "HEAD") == before


@pytest.mark.parametrize("word", ["rotate", "rot"])
def test_the_old_names_are_gone(world, word: str) -> None:
    p = subprocess.run(
        argv("gw", word),
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert p.returncode == 2
    assert "is not a command" in p.stderr
