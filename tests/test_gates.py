"""The two holes in git that are the reason this exists, plus the two gates."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from worktrees import prune, verdicts
from worktrees import repo as R
from worktrees.git import GitError, Refused, guard

ROOT = Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------
# Hole 1: a squash merge inverts `git branch -d`
# --------------------------------------------------------------------------


def test_squashed_and_unmerged_are_indistinguishable_to_git(world) -> None:
    """The premise. Assert the shadow shadows before asserting the fix."""
    world.worktree("squashed")
    wt = world.parent / ".worktrees" / "squashed" / "repo"
    world.commit("feature.txt", "one\n", at=wt)
    world.worktree("unmerged")
    world.commit(
        "other.txt", "two\n", at=world.parent / ".worktrees" / "unmerged" / "repo"
    )

    # main takes squashed's change as one commit, which is what a forge does.
    (world.repo / "feature.txt").write_text("one\n")
    world.git("add", "--", "feature.txt")
    world.git("commit", "--quiet", "-m", "squash of squashed")

    for branch in ("squashed", "unmerged"):
        merged = world.git("branch", "--list", "--merged", "main", branch)
        assert merged == "", f"{branch} reads as merged and should not"
        ahead = world.git("rev-list", "--count", f"main..refs/heads/{branch}")
        assert ahead == "1", f"{branch} is {ahead} ahead, expected 1"

    # The checkout goes first, which is the order any reap uses; a branch in
    # use is refused for a reason that has nothing to do with merging.
    world.git("worktree", "remove", str(wt))
    proc = subprocess.run(
        ["git", "-C", str(world.repo), "branch", "-d", "squashed"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "not fully merged" in proc.stderr
    assert "-D" in proc.stderr, "git points you at the flag that deletes anything"


def test_the_probe_separates_them(world) -> None:
    world.worktree("squashed")
    world.commit(
        "feature.txt", "one\n", at=world.parent / ".worktrees" / "squashed" / "repo"
    )
    world.worktree("unmerged")
    world.commit(
        "other.txt", "two\n", at=world.parent / ".worktrees" / "unmerged" / "repo"
    )
    (world.repo / "feature.txt").write_text("one\n")
    world.git("add", "--", "feature.txt")
    world.git("commit", "--quiet", "-m", "squash of squashed")

    seen = {
        v.branch: v
        for v in verdicts.assess(R.worktrees(), "", "refs/heads/main", "main", False)
    }
    assert seen["squashed"].verdict == verdicts.REMOVE
    assert seen["squashed"].why == "squash-merged"
    assert seen["unmerged"].verdict == verdicts.UNKNOWN
    assert "no upstream" in seen["unmerged"].why


def test_a_branch_that_changed_nothing_is_finished(world) -> None:
    """Short-circuit: branch^{tree} == head^{tree}, whatever history says."""
    world.worktree("noop")
    seen = {
        v.branch: v
        for v in verdicts.assess(R.worktrees(), "", "refs/heads/main", "main", False)
    }
    assert seen["noop"].verdict == verdicts.REMOVE


# --------------------------------------------------------------------------
# Hole 2: `git worktree remove` silently deletes ignored files
# --------------------------------------------------------------------------


def test_git_takes_ignored_files_without_a_word(world) -> None:
    """The premise, reproduced."""
    (world.repo / ".gitignore").write_text(".env\nnode_modules\n")
    world.git("add", "--", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")
    wt = world.worktree("dirty")
    (wt / ".env").write_text("SECRET=1\n")
    (wt / "node_modules").mkdir()
    (wt / "node_modules" / "pkg.js").write_text("x\n")

    assert world.git("status", "--porcelain", at=wt) == ""
    proc = subprocess.run(
        ["git", "-C", str(world.repo), "worktree", "remove", str(wt)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert not wt.exists()


def test_ignored_files_stop_the_verdict(world) -> None:
    (world.repo / ".gitignore").write_text(".env\nnode_modules\n")
    world.git("add", "--", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")
    wt = world.worktree("holds")
    (wt / ".env").write_text("SECRET=1\n")
    (wt / "node_modules").mkdir()
    (wt / "node_modules" / "pkg.js").write_text("x\n")

    v = {
        x.branch: x
        for x in verdicts.assess(R.worktrees(), "", "refs/heads/main", "main", False)
    }["holds"]
    assert v.verdict == verdicts.KEEP
    assert "2 ignored path(s)" in v.why
    assert "--delete-ignored" in v.why

    # --yes must not answer that question; only --delete-ignored does.
    v = {
        x.branch: x
        for x in verdicts.assess(R.worktrees(), "", "refs/heads/main", "main", True)
    }["holds"]
    assert v.verdict == verdicts.REMOVE


def test_ignored_directory_collapses_to_one_entry(world) -> None:
    """--ignored=traditional, so node_modules/ is one line not forty thousand."""
    (world.repo / ".gitignore").write_text("node_modules\n")
    world.git("add", "--", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")
    wt = world.worktree("big")
    (wt / "node_modules").mkdir()
    for i in range(20):
        (wt / "node_modules" / f"{i}.js").write_text("x\n")
    assert R.ignored_paths(str(wt)) == ["node_modules/"]


# --------------------------------------------------------------------------
# Hole 1, the other half: deleting the branch the probe proved
# --------------------------------------------------------------------------


def test_a_branch_that_moved_since_the_verdict_is_not_deleted(world) -> None:
    """`update-ref -d` names the sha the ref must still hold, so a commit
    landing between the judgement and the removal fails the delete."""
    wt = world.worktree("squashed")
    world.commit("f.txt", "one\n", at=wt)
    (world.repo / "f.txt").write_text("one\n")
    world.git("add", "--", "f.txt")
    world.git("commit", "--quiet", "-m", "squash")

    v = {
        x.branch: x
        for x in verdicts.assess(R.worktrees(), "", "refs/heads/main", "main", False)
    }["squashed"]
    assert v.verdict == verdicts.REMOVE

    after = world.commit("late.txt", "late\n", at=wt)
    assert after != v.sha
    with pytest.raises(GitError):
        prune.ref_delete("refs/heads/squashed", v.sha, repo=world.repo)
    assert world.git("rev-parse", "refs/heads/squashed") == after


# --------------------------------------------------------------------------
# Gate 1: a ref naming a branch resolves to that branch
# --------------------------------------------------------------------------


def test_a_tag_cannot_answer_for_a_branch(world) -> None:
    """A1/A7. gitrevisions resolves a bare name as a tag before a branch."""
    world.worktree("feature")
    world.commit("f.txt", "f\n", at=world.parent / ".worktrees" / "feature" / "repo")
    # A tag with the branch's name, on a commit main already contains.
    world.git("tag", "feature", "main")

    v = {
        x.branch: x
        for x in verdicts.assess(R.worktrees(), "", "refs/heads/main", "main", False)
    }["feature"]
    assert v.verdict != verdicts.REMOVE, "the tag answered for the branch"


def test_the_head_ref_is_never_a_bare_name(world) -> None:
    """A3. The prefix is needed on the head ref as well as on the branch."""
    world.git("tag", "main", "main")
    assert R.full_ref("main") == "refs/heads/main"
    world.git("update-ref", "refs/remotes/origin/main", "main")
    assert R.full_ref("origin/main") == "refs/remotes/origin/main"


def test_every_printed_sha_is_a_commit(world) -> None:
    """A2. An annotated tag's sha gives a restore line with an empty tree."""
    world.worktree("done")
    sha = world.git("rev-parse", "refs/heads/done")
    line = prune.restore_line("done")
    assert line == f"git branch done {sha}"
    assert world.git("cat-file", "-t", sha) == "commit"
    assert prune.restore_line("no-such-branch") == ""


# --------------------------------------------------------------------------
# Gate 2: the head branch survives whatever the verdict says
# --------------------------------------------------------------------------


def test_the_head_branch_is_never_proposed(world) -> None:
    """B1. A head branch checked out in a linked worktree."""
    path = world.parent / ".worktrees" / "main-again" / "repo"
    path.parent.mkdir(parents=True)
    world.git("worktree", "add", "--quiet", "--detach", str(path), "main")
    world.git("checkout", "--quiet", "-B", "second", "main", at=path)

    # A worktree on the head branch itself, made the way git allows.
    world.git("worktree", "add", "--quiet", "--detach", str(path.parent / "x"), "main")

    rows = verdicts.assess(R.worktrees(), "", "refs/heads/main", "main", False)
    assert all(v.branch != "main" for v in rows)


def test_a_tag_shadowing_the_head_branch_does_not_shadow_it(world) -> None:
    """B2. `git tag origin/main feature` is the spelling that makes it visible."""
    world.worktree("feature")
    world.commit("f.txt", "f\n", at=world.parent / ".worktrees" / "feature" / "repo")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("tag", "origin/main", "refs/heads/feature")

    assert R.full_ref("origin/main") == "refs/remotes/origin/main"
    v = {
        x.branch: x
        for x in verdicts.assess(
            R.worktrees(), "", R.full_ref("origin/main"), "main", False
        )
    }
    assert v["feature"].verdict != verdicts.REMOVE


def test_a_detached_worktree_is_named_not_mistaken(world) -> None:
    """B7."""
    path = world.parent / ".worktrees" / "detached" / "repo"
    path.parent.mkdir(parents=True)
    world.git("worktree", "add", "--quiet", "--detach", str(path), "main")
    v = verdicts.assess(R.worktrees(), "", "refs/heads/main", "main", False)
    assert len(v) == 1
    assert v[0].branch == ""
    assert v[0].label == "(detached)"
    assert v[0].verdict == verdicts.REMOVE
    assert v[0].why == "its commit is reached by a ref"


# --------------------------------------------------------------------------
# the guard
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ("reset", "--hard", "HEAD"),
        ("clean", "-f"),
        ("clean", "-fdx"),
        ("clean", "--force"),
        ("push", "--force", "origin", "main"),
        ("push", "-f", "origin", "main"),
        ("worktree", "remove", "--force", "/tmp/x"),
        ("worktree", "remove", "-f", "/tmp/x"),
        ("branch", "-D", "x"),
        ("branch", "--delete", "--force", "x"),
        ("update-ref", "-d", "refs/heads/x"),
        ("update-ref", "-d", "refs/heads/x", ""),
        ("update-ref", "-d", "refs/heads/x", "HEAD"),
        ("update-ref", "-d", "refs/heads/x", "main"),
        ("update-ref", "-d", "refs/heads/x", "0e68734"),
        ("update-ref", "-d", "refs/heads/x", "f" * 40, "extra"),
        ("update-ref", "--stdin"),
    ],
)
def test_the_guard_refuses(argv) -> None:
    with pytest.raises(Refused):
        guard(argv)


def test_git_reads_an_empty_old_value_as_no_old_value(world) -> None:
    """The premise behind the sha check, reproduced.

    `update-ref -d <ref> ""` is not a compare-and-delete that fails. git
    takes the branch at exit 0, whatever it pointed at, so a guard counting
    arguments rather than reading the last one passes `branch -D`.
    """
    world.git("branch", "victim", "main")
    subprocess.run(
        ["git", "-C", str(world.repo), "update-ref", "-d", "refs/heads/victim", ""],
        capture_output=True,
        text=True,
        check=True,
    )
    gone = subprocess.run(
        ["git", "-C", str(world.repo), "rev-parse", "--verify", "refs/heads/victim"],
        capture_output=True,
        text=True,
    )
    assert gone.returncode != 0, "git kept the branch, so this premise is stale"

    with pytest.raises(Refused):
        guard(["update-ref", "-d", "refs/heads/victim", ""])


@pytest.mark.parametrize(
    "argv",
    [
        ("push", "--force-with-lease", "origin", "main"),
        ("worktree", "remove", "--", "/tmp/x"),
        ("branch", "-d", "--", "x"),
        ("update-ref", "-d", "refs/heads/x", "0" * 40),
        ("update-ref", "-d", "refs/heads/x", "a" * 64),
        ("clean", "-n"),
        ("status", "--porcelain"),
    ],
)
def test_the_guard_allows(argv) -> None:
    guard(argv)


def test_the_guard_cannot_be_bypassed(world) -> None:
    """It runs on the resolved argv inside the wrapper, not at declaration."""
    from worktrees.git import git

    @git("branch $flag x")
    def sneaky(flag: str) -> None:
        """A caller assembling a refused command from data."""

    with pytest.raises(Refused):
        sneaky("-D")


def test_a_spec_naming_an_unknown_parameter_fails_at_import(world) -> None:
    """A typo in a spec is cheapest to hear about at decoration."""
    from worktrees.git import git

    with pytest.raises(NameError, match=r"\$brnch"):

        @git("branch -d $brnch")
        def typo(branch: str) -> None:
            """The spec and the signature disagree."""


def test_the_spec_must_be_a_string(world) -> None:
    """The bare `@git` form is gone; say so rather than failing in shlex."""
    from worktrees.git import git

    with pytest.raises(TypeError, match="takes the command as a string"):

        @git  # type: ignore[arg-type]
        def bare(ref: str) -> None:
            """Decorated the old way."""


def test_no_unexpected_git_command_ran(world) -> None:
    """The registry is the whole surface; a run stays inside it."""
    from worktrees.git import commands, options

    world.worktree("done")
    verdicts.assess(R.worktrees(), "", "refs/heads/main", "main", False)

    # The canary. An empty log is indistinguishable from a run that never
    # reached git, and would pass every assertion below. Named commands
    # rather than a count, so dropping a redundant call does not fail here.
    for flag in ("--ignored=traditional", "--is-ancestor"):
        assert any(flag in argv for argv in options.log), options.log

    def subcommand(argv: tuple[str, ...]) -> str:
        rest = list(argv[1:])
        if rest[:1] == ["-C"]:
            del rest[:2]
        while rest and rest[0].startswith("--"):
            del rest[0]
        return rest[0]

    declared = {subcommand(("git", *c.shape)) for c in commands()}
    for argv in options.log:
        assert subcommand(argv) in declared, f"unregistered: {' '.join(argv)}"


# --------------------------------------------------------------------------
# a branch that is pushed is not `unknown`
# --------------------------------------------------------------------------


def test_a_tracked_branch_reports_a_count_not_none(world) -> None:
    """`unpushed_count` returning None means "no upstream", and a broken
    `upstream_of` returns None too. Every branch then reads as `unknown` and
    nothing fails, because `ok=(0, 1, 128)` swallows the error.
    """
    upstream = world.root / "upstream.git"
    world.git("init", "--bare", "--quiet", str(upstream), at=world.repo)
    world.git("remote", "add", "origin", str(upstream))
    world.git("push", "--quiet", "--set-upstream", "origin", "main")

    world.git("switch", "--quiet", "--create", "pushed")
    world.commit("p.txt", "p\n")
    world.git("push", "--quiet", "--set-upstream", "origin", "pushed")

    assert R.unpushed_count("pushed") == 0

    world.commit("q.txt", "q\n")
    assert R.unpushed_count("pushed") == 1

    world.git("switch", "--quiet", "--create", "untracked", "main")
    assert R.unpushed_count("untracked") is None


def test_the_upstream_spec_is_a_flag_git_accepts(world) -> None:
    """The spec reaches git verbatim, so a typo in a flag is a typo in the
    command. This asserts on what came back rather than on the exit code.
    """
    upstream = world.root / "up2.git"
    world.git("init", "--bare", "--quiet", str(upstream), at=world.repo)
    world.git("remote", "add", "origin", str(upstream))
    world.git("push", "--quiet", "--set-upstream", "origin", "main")

    out = R.upstream_of("main")
    assert out, "upstream_of failed; the spec is not a command git accepts"
    assert out.out.strip() == "origin/main"


# --------------------------------------------------------------------------
# one git status per worktree, not two
# --------------------------------------------------------------------------


def test_a_worktree_is_read_once(world) -> None:
    """`--ignored=traditional` is a superset of plain `--porcelain`, so asking
    both ways was half of every assessment's git calls.
    """
    from worktrees.git import options

    for name in ("a", "b", "c"):
        world.worktree(name)
    verdicts.assess(R.worktrees(), "", "refs/heads/main", "main", False)

    status = [argv for argv in options.log if "status" in argv]
    assert len(status) == 3, status
    assert all("--ignored=traditional" in argv for argv in status)


def test_the_guard_names_every_rule_it_enforces(world) -> None:
    """The footer --explain prints is generated, so it cannot fall behind."""
    from worktrees.git import RULES

    assert len(RULES) == 7
    for rule in RULES:
        assert rule.named
        assert rule.why
    # Each one refuses something, and says so in its own words.
    assert "update-ref" in "".join(rule.refusal("update-ref") for rule in RULES)


def test_the_readme_names_every_rule(world) -> None:
    """README.md is the third copy of the list. `--explain` generates its
    copy from the rules; this one is written out, so it is diffed instead.
    """
    from worktrees.git import RULES

    block = ROOT.joinpath("README.md").read_text().split("## What it refuses")[1]
    listed = " ".join(block.split("```")[1].split())
    for rule in RULES:
        assert rule.named in listed, rule.named
