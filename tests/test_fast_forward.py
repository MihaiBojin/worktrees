"""fast-forward: the branch you stand on, level with the head ref."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path

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


def test_a_name_that_nests_with_a_branch_is_refused(world) -> None:
    squashed(world)
    world.git("branch", "next/one", "main")
    with pytest.raises(Refusal, match="branch next/one exists"):
        F.fast_forward("next")
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


def test_the_refusal_names_commands_that_keep_the_commits(world) -> None:
    """Pasted in order, they put the commits on a branch and bring the head
    branch level, so a second run has nothing to refuse."""
    with_remote(world)
    local = world.commit("local.txt", "x\n")
    level = world.git("rev-parse", "refs/remotes/origin/main")

    p = gw_ff(world)
    assert p.returncode == 1
    lines = [line.strip() for line in p.stderr.splitlines()]
    commands = [line for line in lines if line.startswith("git ")]
    assert commands == [
        "git switch --create NAME",
        "git branch --force main refs/remotes/origin/main",
    ]
    for command in commands:
        subprocess.run(
            shlex.split(command.replace("NAME", "local-work")),
            cwd=world.repo,
            check=True,
            capture_output=True,
        )

    assert world.git("rev-parse", "refs/heads/local-work") == local
    assert world.git("rev-parse", "refs/heads/main") == level
    world.git("switch", "--quiet", "main")
    assert F.fast_forward().result == F.CURRENT


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


# --------------------------------------------------------------------------
# a partly merged branch
# --------------------------------------------------------------------------


def partly_merged(world, branch: str = "fix-parser") -> tuple[str, str]:
    """A adds x.txt and B changes it; main squash-merges both; then C.

    Returns B, the last merged commit, and C, the tip.
    """
    world.git("switch", "--quiet", "--create", branch)
    world.commit("x.txt", "1\n")
    merged_up_to = world.commit("x.txt", "2\n")
    world.git("switch", "--quiet", "main")
    world.git("merge", "--quiet", "--squash", branch)
    world.git("commit", "--quiet", "-m", f"{branch} (#1)")
    world.git("switch", "--quiet", branch)
    return merged_up_to, world.commit("y.txt", "c\n")


def test_raw_git_rebase_replays_the_merged_commits_and_stops(world) -> None:
    """What the carry below exists for: A is replayed onto a main that has it."""
    partly_merged(world)
    raw = subprocess.run(
        ["git", "rebase", "--no-autostash", "main"],
        cwd=world.repo,
        capture_output=True,
        text=True,
    )
    assert raw.returncode != 0
    assert rebasing(world)


def test_a_partly_merged_branch_drops_its_merged_commits_in_place(world) -> None:
    """Offline, nothing says whose request merged the first commits, so the
    branch keeps its name and any request open from it."""
    merged_up_to, tip = partly_merged(world)
    head = world.git("rev-parse", "main")

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["result"] == "rebased"
    assert payload["branch"] == "fix-parser"
    assert payload["proof"] == "squash"
    assert payload["boundary"] == world.git("rev-parse", "--short", merged_up_to)
    assert (payload["carried"], payload["ahead"]) == (1, 1)
    assert payload["previous_sha"] == world.git("rev-parse", "--short", tip)
    assert world.git("symbolic-ref", "--short", "HEAD") == "fix-parser"
    assert world.git("rev-parse", "HEAD~1") == head
    assert world.git("log", "-1", "--format=%s") == "y.txt"


def stacked(world) -> str:
    """parent adds a.txt, child stacked on it adds b.txt, and main squash-merges
    parent. Returns main's tip."""
    world.git("switch", "--quiet", "--create", "parent")
    world.commit("a.txt", "a\n")
    world.git("switch", "--quiet", "--create", "child")
    world.commit("b.txt", "b\n")
    world.git("switch", "--quiet", "main")
    world.git("merge", "--quiet", "--squash", "parent")
    world.git("commit", "--quiet", "-m", "parent (#1)")
    head = world.git("rev-parse", "HEAD")
    world.git("switch", "--quiet", "child")
    return head


def test_a_stacked_branch_whose_parent_merged_keeps_its_name(world) -> None:
    head = stacked(world)

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["branch"], payload["carried"]) == (
        "rebased",
        "child",
        1,
    )
    assert world.git("rev-parse", "HEAD~1") == head
    assert world.git("log", "-1", "--format=%s") == "b.txt"


def test_the_boundary_is_found_from_the_tip_down(world) -> None:
    """A alone is not upstream, while A and B together are."""
    from worktrees import merged

    merged_up_to, tip = partly_merged(world)
    head = world.git("rev-parse", "main")
    first = world.git("rev-parse", f"{merged_up_to}~1")
    found = merged.boundary(tip, head, repo=world.repo)
    assert found is not None
    assert found[0] == merged_up_to
    assert not merged.squash_matches(first, head, repo=world.repo)
    assert not merged.content_matches(first, head, repo=world.repo)


def test_naming_a_branch_that_keeps_its_name_is_refused(world) -> None:
    partly_merged(world)
    p = gw_ff(world, "next-parser")
    assert p.returncode == 1
    assert "keeps its name, since no merged request of its own marks" in p.stderr
    assert world.git("symbolic-ref", "--short", "HEAD") == "fix-parser"


def test_a_later_commit_that_conflicts_stops_in_place(world) -> None:
    _, tip = partly_merged(world)
    advance(world, "y.txt", "main\n")

    p = gw_ff(world, "--json")
    assert p.returncode == 1
    payload = json.loads(p.stdout)
    assert payload["result"] == "conflict"
    assert payload["branch"] == "fix-parser"
    assert payload["paths"] == ["y.txt"]
    world.git("rebase", "--abort")
    assert world.git("rev-parse", "fix-parser") == tip


def test_a_dirty_partly_merged_branch_is_refused_without_autostash(world) -> None:
    _, tip = partly_merged(world)
    (world.repo / "README").write_text("uncommitted\n")

    p = gw_ff(world, "--json")
    assert p.returncode == 1
    assert p.stdout == ""
    assert "unstaged changes" in p.stderr
    assert world.git("rev-parse", "fix-parser") == tip
    assert world.git("stash", "list") == ""
    assert (world.repo / "README").read_text() == "uncommitted\n"


def test_a_finished_branch_is_found_by_content_alone(world) -> None:
    """Main took the branch's file alongside an edit of its own, so neither
    ancestry nor a patch matches."""
    tip = with_work(world)
    world.git("switch", "--quiet", "main")
    (world.repo / "fix-parser.txt").write_text("w\n")
    (world.repo / "other.txt").write_text("o\n")
    world.git("add", "--", "fix-parser.txt", "other.txt")
    world.git("commit", "--quiet", "-m", "re-import")
    world.git("switch", "--quiet", "fix-parser")

    moved = F.fast_forward(now=NOW)
    assert (moved.result, moved.proof) == (F.ROTATED, "content")
    assert world.git("rev-parse", "refs/heads/fix-parser") == tip


def test_the_source_branch_stashes_are_listed_split_at_the_boundary(world) -> None:
    world.git("switch", "--quiet", "--create", "fix-parser")
    world.commit("x.txt", "1\n")
    world.commit("x.txt", "2\n")
    (world.repo / "README").write_text("early\n")
    world.git("stash", "push", "--quiet", "-m", "early")
    world.git("switch", "--quiet", "main")
    world.git("merge", "--quiet", "--squash", "fix-parser")
    world.git("commit", "--quiet", "-m", "fix-parser (#1)")
    world.git("switch", "--quiet", "fix-parser")
    world.commit("y.txt", "c\n")
    (world.repo / "README").write_text("late\n")
    world.git("stash", "push", "--quiet", "-m", "late")

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    found = {e["subject"]: e["after"] for e in json.loads(p.stdout)["stashes"]}
    assert found == {"On fix-parser: early": False, "On fix-parser: late": True}
    assert len(world.git("stash", "list").splitlines()) == 2


def stash_on_tip(world) -> str:
    """Staged, unstaged and untracked work on the tip, stashed; its sha."""
    (world.repo / "y.txt").write_text("staged\n")
    world.git("add", "y.txt")
    (world.repo / "y.txt").write_text("unstaged\n")
    (world.repo / "u.txt").write_text("untracked\n")
    world.git("stash", "push", "--quiet", "--include-untracked", "-m", "late")
    return world.git("rev-parse", "stash@{0}")


def matching(world, branch: str) -> list[str]:
    from worktrees import prune

    entries = prune.stash_list(repo=world.repo).lines
    return prune.stashes_on(branch, entries, repo=world.repo)


def test_raw_git_rebase_leaves_a_stash_on_no_branch(world) -> None:
    """What the move below exists for: rebased in place, the branch's commits
    are new ones, and the stash made on the old tip matches none of them."""
    merged_up_to, _ = partly_merged(world)
    stash = stash_on_tip(world)
    assert matching(world, "fix-parser") == [stash]
    world.git("rebase", "--quiet", "--no-autostash", "--onto", "main", merged_up_to)
    assert matching(world, "fix-parser") == []
    assert world.git("stash", "list", "--format=%H") == stash


def test_a_stash_made_after_the_boundary_moves_with_its_commit(world) -> None:
    partly_merged(world)
    stash = stash_on_tip(world)

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["result"] == "rebased"
    [entry] = payload["stashes"]
    assert (entry["sha"], entry["after"], entry["kept"]) == (stash, True, "")
    copy = entry["moved"]
    assert world.git("stash", "list", "--format=%H %gs") == (
        f"{copy} On fix-parser: late"
    )
    assert world.git("rev-parse", f"{copy}^1") == world.git("rev-parse", "HEAD")
    assert matching(world, "fix-parser") == [copy]
    world.git("stash", "apply", "--quiet", "--index", copy)
    assert world.git("show", ":y.txt") == "staged"
    assert (world.repo / "y.txt").read_text() == "unstaged\n"
    assert (world.repo / "u.txt").read_text() == "untracked\n"


def test_a_rotation_moves_the_stash_onto_the_new_branch(world, network) -> None:
    merged_up_to, _ = partly_merged(world)
    stash_on_tip(world)
    own_merge(world, network, merged_up_to)

    p = gw_ff_online(world, network, "next-parser")
    assert p.returncode == 0, p.stderr
    copy = world.git("stash", "list", "--format=%H")
    assert f"moved as {copy}" in p.stderr
    assert world.git("stash", "list", "--format=%gs") == "On next-parser: late"
    assert world.git("rev-parse", f"{copy}^1") == world.git("rev-parse", "next-parser")
    assert matching(world, "next-parser") == [copy]
    assert matching(world, "fix-parser") == []


def test_a_stash_that_does_not_apply_to_the_replayed_commit_stays(world) -> None:
    partly_merged(world)
    (world.repo / "README").write_text("stashed\n")
    world.git("stash", "push", "--quiet", "-m", "late")
    stash = world.git("rev-parse", "stash@{0}")
    advance(world, "README", "main\n")

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    [entry] = json.loads(p.stdout)["stashes"]
    head = world.git("rev-parse", "--short", "HEAD")
    assert (entry["moved"], entry["kept"]) == (
        "",
        f"it does not apply cleanly to {head}",
    )
    assert world.git("stash", "list", "--format=%H") == stash


def test_raw_patch_ids_differ_once_the_lines_around_a_change_move(world) -> None:
    """What the context-free patch-id below exists for."""
    from worktrees import merged

    advance(world, "f.txt", "".join(f"{n}\n" for n in range(1, 8)))
    world.git("switch", "--quiet", "--create", "fix-parser")
    world.commit("f.txt", "1\n2\n3\nfour\n5\n6\n7\n")
    advance(world, "f.txt", "1\ntwo\n3\n4\n5\n6\n7\n")
    world.git("rebase", "--quiet", "--no-autostash", "main")

    def ids(spec, base: str, tip: str) -> list[str]:
        patches = spec(base, tip, repo=world.repo).out
        return [
            line.split()[0]
            for line in merged.patch_ids(input=patches, repo=world.repo).lines
        ]

    before = ("main~1", "ORIG_HEAD")
    after = ("main", "HEAD")
    assert ids(merged.upstream_patches, *before) != ids(merged.upstream_patches, *after)
    assert ids(F.bare_patches, *before) == ids(F.bare_patches, *after)


def test_a_stash_moves_when_main_edited_the_lines_around_its_commit(world) -> None:
    advance(world, "f.txt", "".join(f"{n}\n" for n in range(1, 8)))
    partly_merged(world)
    world.commit("f.txt", "1\n2\n3\nfour\n5\n6\n7\n")
    (world.repo / "README").write_text("stashed\n")
    world.git("stash", "push", "--quiet", "-m", "late")
    advance(world, "f.txt", "1\ntwo\n3\n4\n5\n6\n7\n")

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    [entry] = json.loads(p.stdout)["stashes"]
    assert entry["kept"] == ""
    assert world.git("rev-parse", f"{entry['moved']}^1") == world.git(
        "rev-parse", "HEAD"
    )


def test_a_stash_on_a_merge_the_rebase_drops_stays(world) -> None:
    """A rebase replays the commits a merge brought in, and not the merge."""
    partly_merged(world)
    world.git("switch", "--quiet", "--create", "side")
    world.commit("e.txt", "e\n")
    world.git("switch", "--quiet", "fix-parser")
    world.git("merge", "--quiet", "--no-ff", "-m", "merge side", "side")
    (world.repo / "README").write_text("stashed\n")
    world.git("stash", "push", "--quiet", "-m", "late")
    stash = world.git("rev-parse", "stash@{0}")

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    [entry] = json.loads(p.stdout)["stashes"]
    assert (entry["after"], entry["moved"], entry["kept"]) == (
        True,
        "",
        "no replayed commit matches the one it was made on",
    )
    assert world.git("stash", "list", "--format=%H") == stash


def test_a_git_that_cannot_copy_a_stash_leaves_it(world, monkeypatch) -> None:
    """A git before 2.40 has no `merge-tree --merge-base`."""
    from worktrees.errors import GitError

    def old_git(*args, **kwargs):
        raise GitError(
            "git merge-tree exited 129:\nerror: unknown option `merge-base=abc'"
        )

    partly_merged(world)
    stash = stash_on_tip(world)
    monkeypatch.setattr(F, "merge_tree", old_git)

    moved = F.fast_forward()
    assert moved.result == F.REBASED
    [entry] = moved.stashes
    assert (entry["moved"], entry["kept"]) == (
        "",
        "git could not copy it: error: unknown option `merge-base=abc'",
    )
    assert world.git("stash", "list", "--format=%H") == stash


def test_a_carry_that_stops_lists_the_stashes_and_moves_none(world) -> None:
    partly_merged(world)
    stash = stash_on_tip(world)
    advance(world, "y.txt", "main\n")

    p = gw_ff(world, "--json")
    assert p.returncode == 1
    payload = json.loads(p.stdout)
    assert payload["result"] == "conflict"
    [entry] = payload["stashes"]
    assert (entry["sha"], entry["moved"], entry["kept"]) == (
        stash,
        "",
        "the rebase stopped before any stash moved",
    )
    assert world.git("stash", "list", "--format=%H") == stash
    world.git("rebase", "--abort")


# --------------------------------------------------------------------------
# --autostash
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "shape, result", [("partly merged", "rebased"), ("finished", "rotated")]
)
def test_autostash_carries_uncommitted_work_across(world, shape, result) -> None:
    if shape == "finished":
        squashed(world)
    else:
        partly_merged(world)
    (world.repo / "README").write_text("uncommitted\n")
    (world.repo / "loose.txt").write_text("untracked\n")

    p = gw_ff(world, "--autostash", "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["result"] == result
    assert world.git("symbolic-ref", "--short", "HEAD") == payload["branch"]
    assert (world.repo / "README").read_text() == "uncommitted\n"
    assert (world.repo / "loose.txt").read_text() == "untracked\n"
    assert world.git("stash", "list") == ""


def test_autostash_carries_uncommitted_work_through_a_rebase(world) -> None:
    with_work(world)
    advance(world)
    (world.repo / "README").write_text("uncommitted\n")

    p = gw_ff(world, "--autostash", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["result"] == "rebased"
    assert (world.repo / "README").read_text() == "uncommitted\n"
    assert world.git("stash", "list") == ""


def test_an_autostash_that_conflicts_is_reported_by_sha(world) -> None:
    dirty_and_behind(world)
    tip = world.git("rev-parse", "HEAD")

    p = gw_ff(world, "--autostash", "--json")
    assert p.returncode == 1
    payload = json.loads(p.stdout)
    assert payload["result"] == "stash-conflict"
    assert payload["paths"] == ["README"]
    sha = payload["stash"]
    assert world.git("rev-parse", f"{sha}^1") == tip
    assert world.git("stash", "list", "--format=%H") == sha
    assert f"git stash apply {sha}" in p.stderr
    assert "stash pop" not in p.stderr
    assert "stash@{" not in p.stderr


def test_an_autostash_that_conflicts_on_the_head_branch_is_reported(world) -> None:
    with_remote(world)
    world.git("switch", "--quiet", "--create", "upstream", "main")
    moved_to = world.commit("README", "remote\n")
    world.git("update-ref", "refs/remotes/origin/main", moved_to)
    world.git("switch", "--quiet", "main")
    before = world.git("rev-parse", "HEAD")
    (world.repo / "README").write_text("uncommitted\n")

    p = gw_ff(world, "--autostash", "--json")
    assert p.returncode == 1
    payload = json.loads(p.stdout)
    assert payload["result"] == "stash-conflict"
    assert world.git("rev-parse", f"{payload['stash']}^1") == before
    assert world.git("rev-parse", "HEAD") == moved_to


def test_an_autostash_left_by_rebase_continue_is_reported_next_run(world) -> None:
    """The skill finishes a stopped rebase with git rebase --continue, which
    re-applies the autostash after gw has returned."""
    world.commit("notes.txt", "base\n")
    tip = conflicting(world)
    advance(world, "notes.txt", "main\n")
    (world.repo / "notes.txt").write_text("uncommitted\n")

    stopped = gw_ff(world, "--autostash", "--json")
    assert json.loads(stopped.stdout)["result"] == "conflict"
    (world.repo / "README").write_text("both\n")
    world.git("add", "--", "README")
    subprocess.run(
        ["git", "rebase", "--continue"],
        cwd=world.repo,
        capture_output=True,
        env={**os.environ, "GIT_EDITOR": "true"},
    )
    assert not rebasing(world)

    p = gw_ff(world, "--json")
    assert p.returncode == 1
    payload = json.loads(p.stdout)
    assert payload["result"] == "stash-conflict"
    assert payload["paths"] == ["notes.txt"]
    assert world.git("rev-parse", f"{payload['stash']}^1") == tip


# --------------------------------------------------------------------------
# live evidence
# --------------------------------------------------------------------------


def on_forge(world, host: str = "github.com") -> None:
    world.git("remote", "add", "origin", f"git@{host}:owner/repo.git")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")


def merged_row(branch: str, source: str, merge: str) -> dict:
    """A GitHub pull request from `branch` at `source`, merged as `merge`."""
    return {
        "number": 17,
        "state": "MERGED",
        "url": "https://github.com/owner/repo/pull/17",
        "headRefName": branch,
        "headRefOid": source,
        "baseRefName": "main",
        "mergedAt": "2026-09-28T00:00:00Z",
        "mergeCommit": {"oid": merge},
        "headRepository": {"nameWithOwner": "owner/repo"},
        "isCrossRepository": False,
    }


def gw_ff_online(world, network, *args: str, **extra: str):
    return subprocess.run(
        argv("gw fast-forward", *args),
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=network.env(**extra),
    )


def test_a_branch_the_forge_merged_moves_on_where_local_proof_fails(
    world, network
) -> None:
    """The file was edited as the pull request merged, so nothing local
    matches."""
    tip = with_work(world)
    world.git("switch", "--quiet", "main")
    squash = world.commit("fix-parser.txt", "edited at merge\n")
    world.git("switch", "--quiet", "fix-parser")
    on_forge(world)
    network.answer({"fix-parser": [merged_row("fix-parser", tip, squash)]})

    p = gw_ff_online(world, network, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["proof"]) == ("rotated", "forge")
    assert world.git("rev-parse", "refs/heads/fix-parser") == tip


def test_the_forge_marks_where_a_squash_merge_ended(world, network) -> None:
    """Main edited a line inside A's diff context before A merged, so the
    patch no longer matches and the file differs: only the forge knows."""
    from worktrees import merged

    world.commit("f.txt", "".join(f"{n}\n" for n in range(1, 11)))
    world.git("switch", "--quiet", "--create", "fix-parser")
    first = world.commit(
        "f.txt", (world.repo / "f.txt").read_text().replace("6\n", "six\n")
    )
    world.git("switch", "--quiet", "main")
    world.commit("f.txt", (world.repo / "f.txt").read_text().replace("3\n", "three\n"))
    world.git("merge", "--quiet", "--squash", "fix-parser")
    world.git("commit", "--quiet", "-m", "fix-parser (#17)")
    squash = world.git("rev-parse", "HEAD")
    world.git("switch", "--quiet", "fix-parser")
    tip = world.commit("y.txt", "c\n")
    assert merged.boundary(tip, squash, repo=world.repo) is None
    on_forge(world)
    network.answer({"fix-parser": [merged_row("fix-parser", first, squash)]})

    p = gw_ff_online(world, network, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["result"] == "rotated"
    assert payload["proof"] == "forge"
    assert payload["boundary"] == world.git("rev-parse", "--short", first)
    assert payload["carried"] == 1
    assert world.git("rev-parse", "HEAD~1") == squash


@pytest.mark.parametrize("host", ["github.com", "gitlab.com"])
def test_the_live_query_asks_for_merged_requests(world, network, host) -> None:
    """--limit 1 of any state would hide a merged request behind a newer one
    opened from the same branch."""
    tip = with_work(world)
    advance(world)
    on_forge(world, host)
    network.answer({"$api": {carriers(host, tip): []}})

    p = gw_ff_online(world, network, "--json")
    assert p.returncode == 0, p.stderr
    queries = [
        c for c in network.network_calls() if c[1:3] in (["pr", "list"], ["mr", "list"])
    ]
    assert len(queries) == 1, network.network_calls()
    query = queries[0]
    if host == "github.com":
        assert query[query.index("--state") + 1] == "merged"
    else:
        assert "--merged" in query and "--all" not in query


def test_a_failed_fetch_refuses_with_the_same_run_offline(world, network) -> None:
    squashed(world)
    on_forge(world)
    before = world.git("rev-parse", "HEAD")

    p = gw_ff_online(world, network, "next", "--from", "has space", WT_FETCH_CODE="128")
    assert p.returncode == 1
    assert p.stdout == ""
    assert "origin could not be fetched" in p.stderr
    assert p.stderr.rstrip().endswith(
        "Run: WORKTREES_OFFLINE=1 gw fast-forward next --from 'has space'"
    )
    assert world.git("rev-parse", "HEAD") == before

    offline = gw_ff_online(world, network, "next", "--json", WORKTREES_OFFLINE="1")
    assert offline.returncode == 0, offline.stderr
    assert json.loads(offline.stdout)["result"] == "rotated"


@pytest.mark.parametrize("failure", ["signed out", "query"])
def test_a_forge_that_cannot_be_asked_refuses(world, network, failure) -> None:
    with_work(world)
    advance(world)
    on_forge(world)
    before = world.git("rev-parse", "HEAD")
    extra = {"WT_AUTH_CODE": "1"} if failure == "signed out" else {}
    if failure == "query":
        network.answer({"fix-parser": 1})

    p = gw_ff_online(world, network, **extra)
    assert p.returncode == 1
    assert p.stdout == ""
    reason = (
        "gh is unavailable or signed out"
        if failure == "signed out"
        else "the forge could not be asked"
    )
    assert reason in p.stderr
    assert p.stderr.rstrip().endswith("Run: WORKTREES_OFFLINE=1 gw fast-forward")
    assert world.git("rev-parse", "HEAD") == before


def test_a_merge_after_the_last_sync_is_fetched_within_the_ttl(world) -> None:
    """A pull request merged a moment ago is the usual reason to run this, so
    it fetches whatever the last sync's age."""
    from conftest import online

    upstream = world.root / "upstream.git"
    world.git("init", "--quiet", "--bare", str(upstream))
    world.git("remote", "add", "origin", str(upstream))
    world.git("push", "--quiet", "origin", "main")
    world.git("fetch", "--quiet", "origin")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    with_work(world)
    env = online(world)
    synced = subprocess.run(
        argv("gw sync"), cwd=world.repo, env=env, capture_output=True, text=True
    )
    assert synced.returncode == 0, synced.stderr

    other = world.root / "other"
    world.git("clone", "--quiet", str(upstream), str(other))
    # A squash of its own: under the fixture's fixed dates, a commit with the
    # branch's message would be the branch's commit.
    (other / "fix-parser.txt").write_text("w\n")
    world.git("add", "--", "fix-parser.txt", at=other)
    world.git("commit", "--quiet", "-m", "fix-parser (#1)", at=other)
    world.git("push", "--quiet", "origin", "main", at=other)

    p = subprocess.run(
        argv("gw fast-forward", "--json"),
        cwd=world.repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["proof"]) == ("rotated", "squash")


# --------------------------------------------------------------------------
# a partly merged branch whose own request merged
# --------------------------------------------------------------------------


def own_merge(world, network, source: str, merge: str = "main") -> None:
    """fix-parser's own pull request, from `source`, squash-merged as `merge`."""
    on_forge(world)
    squash = world.git("rev-parse", merge)
    network.answer({"fix-parser": [merged_row("fix-parser", source, squash)]})


def test_a_branch_whose_own_request_merged_carries_the_rest(world, network) -> None:
    """The name stays with the merged request, so the commits after it move."""
    merged_up_to, tip = partly_merged(world)
    head = world.git("rev-parse", "main")
    own_merge(world, network, merged_up_to)

    p = gw_ff_online(world, network, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["proof"]) == ("rotated", "forge")
    assert payload["boundary"] == world.git("rev-parse", "--short", merged_up_to)
    assert (payload["carried"], payload["ahead"]) == (1, 1)
    assert payload["previous"] == "fix-parser"
    assert STAMP.match(payload["branch"])
    assert world.git("symbolic-ref", "--short", "HEAD") == payload["branch"]
    assert world.git("rev-parse", "HEAD~1") == head
    # The source branch is left exactly where it was.
    assert world.git("rev-parse", "refs/heads/fix-parser") == tip


def test_a_carry_takes_the_name_given(world, network) -> None:
    merged_up_to, _ = partly_merged(world)
    own_merge(world, network, merged_up_to)
    p = gw_ff_online(world, network, "next-parser", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["branch"] == "next-parser"


def test_a_carried_commit_that_conflicts_stops_on_the_new_branch(
    world, network
) -> None:
    merged_up_to, tip = partly_merged(world)
    squash = world.git("rev-parse", "main")
    advance(world, "y.txt", "main\n")
    own_merge(world, network, merged_up_to, squash)

    p = gw_ff_online(world, network, "next-parser", "--json")
    assert p.returncode == 1
    payload = json.loads(p.stdout)
    assert payload["result"] == "conflict"
    assert payload["branch"] == "next-parser"
    assert payload["paths"] == ["y.txt"]
    world.git("rebase", "--abort")
    assert world.git("symbolic-ref", "--short", "HEAD") == "next-parser"
    assert world.git("rev-parse", "next-parser") == tip
    assert world.git("rev-parse", "fix-parser") == tip


def test_a_dirty_carry_is_refused_before_the_new_branch(world, network) -> None:
    merged_up_to, _ = partly_merged(world)
    own_merge(world, network, merged_up_to)
    (world.repo / "README").write_text("uncommitted\n")
    branches = world.git("for-each-ref", "--format=%(refname)", "refs/heads")

    p = gw_ff_online(world, network, "--json")
    assert p.returncode == 1
    assert p.stdout == ""
    assert "--autostash carries the changes across" in p.stderr
    assert world.git("for-each-ref", "--format=%(refname)", "refs/heads") == branches
    assert world.git("stash", "list") == ""
    assert (world.repo / "README").read_text() == "uncommitted\n"


def test_a_stacked_branch_with_no_merged_request_keeps_its_name(world, network) -> None:
    """Its parent's request merged, not its own."""
    head = stacked(world)
    on_forge(world)

    p = gw_ff_online(world, network, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["branch"]) == ("rebased", "child")
    assert world.git("rev-parse", "HEAD~1") == head


# --------------------------------------------------------------------------
# a stacked branch whose parent gained commits before it merged
# --------------------------------------------------------------------------


def carriers(host: str, sha: str) -> str:
    """The API path that lists the requests that carried `sha`."""
    if host == "github.com":
        return f"repos/owner/repo/commits/{sha}/pulls"
    return f"projects/owner%2Frepo/repository/commits/{sha}/merge_requests"


def carried(host: str, merge: str, commits: list[str], **changes) -> dict:
    """The forge's answers for parent's request, merged as `merge`."""
    if host == "github.com":
        row = {
            "number": 1,
            "state": "closed",
            "merged_at": "2026-11-08T00:00:00Z",
            "head": {"ref": "parent", "sha": commits[-1]},
            "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
            "merge_commit_sha": merge,
        }
        listed = "repos/owner/repo/pulls/1/commits?per_page=100"
        shas = [{"sha": c} for c in commits]
    else:
        row = {
            "iid": 1,
            "state": "merged",
            "web_url": "https://gitlab.com/owner/repo/-/merge_requests/1",
            "source_branch": "parent",
            "target_branch": "main",
            "merge_commit_sha": merge,
        }
        listed = "projects/owner%2Frepo/merge_requests/1/commits?per_page=100"
        shas = [{"id": c} for c in commits]
    return {carriers(host, commits[0]): [{**row, **changes}], listed: shas}


def outgrown(world) -> tuple[str, str, str]:
    """parent commits A, child stacks B on it, parent goes on with A2, and
    main squash-merges parent. Returns A, A2 and the squash."""
    world.git("switch", "--quiet", "--create", "parent")
    first = world.commit("x.txt", "1\n")
    world.git("switch", "--quiet", "--create", "child")
    world.commit("b.txt", "b\n")
    world.git("switch", "--quiet", "parent")
    later = world.commit("x.txt", "2\n")
    world.git("switch", "--quiet", "main")
    world.git("merge", "--quiet", "--squash", "parent")
    world.git("commit", "--quiet", "-m", "parent (#1)")
    squash = world.git("rev-parse", "HEAD")
    world.git("switch", "--quiet", "child")
    return first, later, squash


def test_raw_git_replays_a_parent_commit_the_squash_outgrew(world) -> None:
    """What the forge is asked for: A's patch is not the squash's, and x.txt
    differs from main, so git replays A and stops."""
    from worktrees import merged

    _, _, squash = outgrown(world)
    tip = world.git("rev-parse", "child")
    assert merged.boundary(tip, squash, repo=world.repo) is None
    raw = subprocess.run(
        ["git", "rebase", "--no-autostash", "main"],
        cwd=world.repo,
        capture_output=True,
        text=True,
    )
    assert raw.returncode != 0
    assert rebasing(world)


@pytest.mark.parametrize("host", ["github.com", "gitlab.com"])
def test_the_parents_merged_request_marks_its_commits(world, network, host) -> None:
    first, later, squash = outgrown(world)
    on_forge(world, host)
    network.answer({"$api": carried(host, squash, [first, later])})

    p = gw_ff_online(world, network, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["branch"], payload["proof"]) == (
        "rebased",
        "child",
        "forge",
    )
    assert payload["boundary"] == world.git("rev-parse", "--short", first)
    assert payload["carried"] == 1
    assert world.git("rev-parse", "HEAD~1") == squash
    assert world.git("log", "-1", "--format=%s") == "b.txt"


RELEASE = {"base": {"ref": "release", "repo": {"full_name": "owner/repo"}}}


def test_a_parents_request_stacked_on_another_branch_marks_its_commits(
    world, network
) -> None:
    """The parent's request records the branch it was stacked on, and the
    forge landed its squash on main's first-parent history."""
    first, later, squash = outgrown(world)
    on_forge(world)
    network.answer({"$api": carried("github.com", squash, [first, later], **RELEASE)})

    p = gw_ff_online(world, network, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["proof"]) == ("rebased", "forge")
    assert payload["boundary"] == world.git("rev-parse", "--short", first)


def test_a_request_into_another_branch_off_the_first_parents_marks_nothing(
    world, network
) -> None:
    """main reaches the request's merge commit only through a merge of its
    own, which is not the forge landing it there."""
    first, later, _ = outgrown(world)
    world.git("switch", "--quiet", "--create", "release", "main")
    side = world.commit("r.txt", "r\n")
    world.git("switch", "--quiet", "main")
    world.git("merge", "--quiet", "--no-ff", "-m", "merge release", "release")
    world.git("switch", "--quiet", "child")
    on_forge(world)
    network.answer({"$api": carried("github.com", side, [first, later], **RELEASE)})

    p = gw_ff_online(world, network, "--json")
    assert p.returncode == 1
    assert json.loads(p.stdout)["result"] == "conflict"


@pytest.mark.parametrize(
    "change",
    [
        {"base": {"ref": "main", "repo": {"full_name": "fork/repo"}}},
        {"merged_at": None},
        {"head": {"ref": "child", "sha": "x"}},
        {"merge_commit_sha": "0" * 40},
    ],
    ids=["repository", "unmerged", "its own", "merge not in head"],
)
def test_a_request_that_does_not_verify_marks_nothing(world, network, change) -> None:
    first, later, squash = outgrown(world)
    on_forge(world)
    network.answer({"$api": carried("github.com", squash, [first, later], **change)})

    p = gw_ff_online(world, network, "--json")
    assert p.returncode == 1
    assert json.loads(p.stdout)["result"] == "conflict"


def test_a_forge_that_cannot_say_what_carried_the_branch_refuses(
    world, network
) -> None:
    outgrown(world)
    on_forge(world)
    before = world.git("rev-parse", "HEAD")

    p = gw_ff_online(world, network)
    assert p.returncode == 1
    assert "the forge could not be asked which request carried" in p.stderr
    assert p.stderr.rstrip().endswith("Run: WORKTREES_OFFLINE=1 gw fast-forward")
    assert world.git("rev-parse", "HEAD") == before


def unpushed(world) -> str:
    """topic commits t.txt nobody pushed, and main goes on. Returns its sha."""
    world.git("switch", "--quiet", "--create", "topic")
    sha = world.commit("t.txt", "t\n")
    world.git("switch", "--quiet", "main")
    world.commit("m.txt", "m\n")
    world.git("switch", "--quiet", "topic")
    return sha


@pytest.mark.parametrize(
    ("host", "body"),
    [
        ("github.com", {"message": "No commit found for SHA: {sha}", "status": "422"}),
        ("gitlab.com", {"message": "404 Commit Not Found"}),
    ],
    ids=["github", "gitlab"],
)
def test_a_commit_the_forge_does_not_hold_was_carried_by_none(
    world, network, host, body
) -> None:
    """The answer each forge gives for a commit nobody pushed."""
    sha = unpushed(world)
    on_forge(world, host)
    answer = {**body, "message": body["message"].format(sha=sha)}
    network.answer({"$api": {carriers(host, sha): {"$fail": answer}}})

    p = gw_ff_online(world, network, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["branch"]) == ("rebased", "topic")
    assert world.git("rev-parse", "HEAD~1") == world.git("rev-parse", "main")


def test_another_forge_error_still_refuses(world, network) -> None:
    sha = unpushed(world)
    on_forge(world)
    failure = {"$fail": {"message": "Not Found", "status": "404"}}
    network.answer({"$api": {carriers("github.com", sha): failure}})

    p = gw_ff_online(world, network)
    assert p.returncode == 1
    assert "the forge could not be asked which request carried" in p.stderr


# --------------------------------------------------------------------------
# a stacked branch the forge rewrote when its parent merged
# --------------------------------------------------------------------------


def restacked(world) -> str:
    """parent adds a.txt and child, stacked on it, adds b.txt; both are
    pushed. Then, elsewhere, main squash-merges parent and the forge rewrites
    child onto the squash under a committer of its own, as GitHub does. This
    clone has fetched and still holds child as it pushed it. Returns
    parent's tip."""
    upstream = world.root / "upstream.git"
    world.git("init", "--quiet", "--bare", str(upstream))
    world.git("remote", "add", "origin", str(upstream))
    world.git("push", "--quiet", "origin", "main")
    world.git("switch", "--quiet", "--create", "parent")
    parent = world.commit("a.txt", "a\n")
    world.git("switch", "--quiet", "--create", "child")
    world.commit("b.txt", "b\n")
    world.git("push", "--quiet", "--set-upstream", "origin", "parent", "child")

    forge = world.root / "forge"
    world.git("clone", "--quiet", str(upstream), str(forge))
    world.git("merge", "--quiet", "--squash", "origin/parent", at=forge)
    world.git("commit", "--quiet", "-m", "parent (#1)", at=forge)
    as_github = {**os.environ, "GIT_COMMITTER_NAME": "GitHub"}
    for step in (
        ["rebase", "--quiet", "--onto", "main", "origin/parent", "origin/child"],
        ["push", "--quiet", "--force", "origin", "HEAD:child", "main"],
    ):
        subprocess.run(
            ["git", "-C", str(forge), *step],
            check=True,
            capture_output=True,
            env=as_github,
        )

    world.git("fetch", "--quiet", "origin")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return parent


def leased_push(world) -> subprocess.CompletedProcess[str]:
    """The push ship-it and the fast-forward skill make for a rebased branch."""
    return subprocess.run(
        [
            "git",
            "push",
            "--porcelain",
            "--force-with-lease",
            "--force-if-includes",
            "origin",
            "child",
        ],
        cwd=world.repo,
        capture_output=True,
        text=True,
    )


def test_raw_git_rebase_leaves_a_branch_its_lease_push_refuses(world) -> None:
    """What taking the forge's copy is for: the same commits, replayed here,
    are a push --force-if-includes refuses."""
    parent = restacked(world)
    world.git("rebase", "--quiet", "--onto", "origin/main", parent, "child")
    assert world.git("diff", "child", "origin/child") == ""
    assert world.git("rev-parse", "child") != world.git("rev-parse", "origin/child")

    p = leased_push(world)
    assert p.returncode != 0
    assert "[rejected]" in p.stdout


def test_a_stacked_branch_the_forge_rewrote_takes_its_copy(world) -> None:
    parent = restacked(world)
    copy = world.git("rev-parse", "origin/child")

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["adopted"]) == (
        "rebased",
        "refs/remotes/origin/child",
    )
    assert payload["boundary"] == world.git("rev-parse", "--short", parent)
    assert payload["carried"] == 1
    assert world.git("rev-parse", "child") == copy
    assert leased_push(world).returncode == 0


def test_a_commit_made_after_the_push_lands_on_the_forges_copy(world) -> None:
    restacked(world)
    copy = world.git("rev-parse", "origin/child")
    world.commit("c.txt", "c\n")

    p = gw_ff(world)
    assert p.returncode == 0, p.stderr
    assert "moved to origin/child, its commits rewritten there" in p.stdout
    assert world.git("rev-parse", "child~1") == copy
    assert world.git("log", "-1", "--format=%s") == "c.txt"
    assert leased_push(world).returncode == 0


def test_a_branch_already_rebased_onto_the_squash_takes_the_copy(world) -> None:
    """A branch an earlier fast-forward rebased in place is level with the
    head ref and still diverges from the forge's copy."""
    parent = restacked(world)
    world.git("rebase", "--quiet", "--onto", "origin/main", parent, "child")

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["adopted"]) == (
        "rebased",
        "refs/remotes/origin/child",
    )
    assert world.git("rev-parse", "child") == world.git("rev-parse", "origin/child")
    assert leased_push(world).returncode == 0


def test_a_copy_this_branch_pushed_is_not_taken_back(world) -> None:
    """A branch rebased here after its own push is newer than its remote
    copy, and its reflog says so."""
    restacked(world)
    world.git("reset", "--quiet", "--hard", "origin/child")
    world.git("push", "--quiet", "--force-with-lease", "origin", "child")
    advance(world, "later.txt")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("rebase", "--quiet", "origin/main")
    rebased = world.git("rev-parse", "child")

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["adopted"]) == ("current", "")
    assert world.git("rev-parse", "child") == rebased


def test_a_copy_holding_a_commit_the_branch_lacks_is_not_taken(world) -> None:
    restacked(world)
    forge = world.root / "forge"
    world.commit("d.txt", "d\n", at=forge)
    world.git("push", "--quiet", "origin", "HEAD:child", at=forge)
    world.git("fetch", "--quiet", "origin")

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["adopted"]) == ("rebased", "")
    assert world.git("rev-parse", "child~1") == world.git("rev-parse", "origin/main")
    assert world.git("rev-parse", "child") != world.git("rev-parse", "origin/child")


def test_a_copy_main_has_since_outgrown_is_what_the_branch_is_level_with(
    world,
) -> None:
    """GitHub restacks onto the parent's merge, so a later merge to main
    leaves the copy below the head ref; the next run rebases onto main."""
    restacked(world)
    forge = world.root / "forge"
    world.git("switch", "--quiet", "main", at=forge)
    world.commit("later.txt", "l\n", at=forge)
    world.git("push", "--quiet", "origin", "main", at=forge)
    world.git("fetch", "--quiet", "origin")
    copy = world.git("rev-parse", "origin/child")

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["base"], payload["adopted"]) == (
        "rebased",
        "refs/remotes/origin/child",
        "refs/remotes/origin/child",
    )
    assert world.git("rev-parse", "child") == copy
    assert leased_push(world).returncode == 0

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["base"], payload["adopted"]) == (
        "rebased",
        "refs/remotes/origin/main",
        "",
    )
    assert world.git("rev-parse", "child~1") == world.git("rev-parse", "origin/main")
    assert leased_push(world).returncode == 0


def test_a_stash_on_a_rebased_commit_moves_to_the_copy_it_takes(world) -> None:
    parent = restacked(world)
    world.git("rebase", "--quiet", "--onto", "origin/main", parent, "child")
    (world.repo / "b.txt").write_text("b, then more\n")
    world.git("stash", "push", "--quiet")
    made = world.git("rev-parse", "stash@{0}")

    p = gw_ff(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["adopted"] == "refs/remotes/origin/child"
    [entry] = payload["stashes"]
    assert (entry["sha"], entry["after"], entry["kept"]) == (made, True, "")
    assert world.git("rev-parse", f"{entry['moved']}^1") == world.git(
        "rev-parse", "origin/child"
    )


def restacking(world, network, stack: object) -> tuple[str, Path]:
    """`stacked` on a GitHub remote whose copy of child still holds parent's
    commit, with child's open pull request #18 describing `stack`. Returns
    the copy GitHub's rewrite will make, and the file counting fetches."""
    stacked(world)
    on_forge(world)
    old = world.git("rev-parse", "child")
    world.git("update-ref", "refs/remotes/origin/child", old)
    world.git("switch", "--quiet", "--detach", "main")
    subprocess.run(
        ["git", "cherry-pick", "child"],
        cwd=world.repo,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_COMMITTER_NAME": "GitHub"},
    )
    rewrite = world.git("rev-parse", "HEAD")
    world.git("switch", "--quiet", "child")
    row = {
        **merged_row("child", old, ""),
        "number": 18,
        "state": "OPEN",
        "url": "https://github.com/owner/repo/pull/18",
        "mergedAt": None,
        "mergeCommit": None,
    }
    network.answer(
        {
            "child": {"merged": [], "all": [row]},
            "$api": {"repos/owner/repo/pulls/18": {"number": 18, "stack": stack}},
        }
    )
    return rewrite, world.root / "fetches"


STACK = {"id": 3470482, "number": 5, "position": 2, "size": 2}


def test_a_run_straight_after_the_merge_waits_for_githubs_rewrite(
    world, network
) -> None:
    rewrite, fetches = restacking(world, network, STACK)
    network.intercept(
        "    if command[:1] == ['fetch']:\n"
        f"        counter = pathlib.Path({str(fetches)!r})\n"
        "        seen = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "        counter.write_text(str(seen))\n"
        "        if seen == 2:\n"
        "            subprocess.check_call([real_git, *prefix, 'update-ref', "
        f"'refs/remotes/origin/child', {rewrite!r}])\n"
    )

    p = gw_ff_online(world, network, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["adopted"]) == (
        "rebased",
        "refs/remotes/origin/child",
    )
    assert world.git("rev-parse", "child") == rewrite
    assert fetches.read_text() == "2"


def test_a_branch_in_no_native_stack_is_rebased_without_waiting(world, network) -> None:
    restacking(world, network, None)

    p = gw_ff_online(world, network, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["result"], payload["adopted"]) == ("rebased", "")
    assert world.git("rev-parse", "child~1") == world.git("rev-parse", "main")
    fetches = [c for c in network.network_calls() if "fetch" in c]
    assert len(fetches) == 1
    assert any("repos/owner/repo/pulls/18" in c for c in network.network_calls())


def test_a_rewrite_that_never_arrives_leaves_the_branch(
    world, network, monkeypatch
) -> None:
    restacking(world, network, STACK)
    tip = world.git("rev-parse", "child")
    for key, value in network.env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("WORKTREES_OFFLINE", raising=False)
    monkeypatch.setattr(F, "REWRITE_WAIT", 0)

    with pytest.raises(F.Unfetched, match="has not yet rewritten"):
        F.fast_forward(live=True)
    assert world.git("rev-parse", "child") == tip
    asked = network.network_calls()
    assert any("repos/owner/repo/pulls/18" in call for call in asked)
    assert any("fetch" in call for call in asked)
