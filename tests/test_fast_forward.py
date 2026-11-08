"""fast-forward: the branch you stand on, level with the head ref."""

from __future__ import annotations

import json
import os
import re
import shlex
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


@pytest.mark.parametrize(
    "change",
    [
        {"base": {"ref": "release", "repo": {"full_name": "owner/repo"}}},
        {"base": {"ref": "main", "repo": {"full_name": "fork/repo"}}},
        {"merged_at": None},
        {"head": {"ref": "child", "sha": "x"}},
        {"merge_commit_sha": "0" * 40},
    ],
    ids=["target", "repository", "unmerged", "its own", "merge not in head"],
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
