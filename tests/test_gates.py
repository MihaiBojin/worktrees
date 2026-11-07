"""The two holes in git that are the reason this exists, plus the two gates."""

from __future__ import annotations

import json
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
        for v in verdicts.assess(R.worktrees(), "refs/heads/main", "main", frozenset())
    }
    assert seen["squashed"].verdict == verdicts.REMOVE
    assert seen["squashed"].why == (
        "its tree or combined patch is already in the head branch"
    )
    assert seen["unmerged"].verdict == verdicts.KEEP
    assert seen["unmerged"].why == "no proof that its change is in the head branch"


def test_a_branch_that_changed_nothing_is_empty(world) -> None:
    """Its tip is on main's first-parent line, which git cannot tell from a
    fast-forward merge, so it is `empty` and needs --delete empty."""
    world.worktree("noop")
    seen = {
        v.branch: v
        for v in verdicts.assess(R.worktrees(), "refs/heads/main", "main", frozenset())
    }
    assert (seen["noop"].verdict, seen["noop"].eligible) == (verdicts.EMPTY, False)
    allowed = frozenset({"empty"})
    seen = {
        v.branch: v
        for v in verdicts.assess(R.worktrees(), "refs/heads/main", "main", allowed)
    }
    assert (seen["noop"].verdict, seen["noop"].eligible) == (verdicts.EMPTY, True)


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


def test_ignored_files_stop_eligibility(world) -> None:
    (world.repo / ".gitignore").write_text(".env\nnode_modules\n")
    world.git("add", "--", ".gitignore")
    world.git("commit", "--quiet", "-m", "ignore")
    wt = world.finished("holds")
    (wt / ".env").write_text("SECRET=1\n")
    (wt / "node_modules").mkdir()
    (wt / "node_modules" / "pkg.js").write_text("x\n")

    v = {
        x.branch: x
        for x in verdicts.assess(R.worktrees(), "refs/heads/main", "main", frozenset())
    }["holds"]
    assert (v.verdict, v.eligible) == (verdicts.REMOVE, False)
    assert "2 ignored path(s)" in v.why
    assert "--delete ignored-files" in v.why

    # --yes must not answer that question; only --delete ignored-files does.
    v = {
        x.branch: x
        for x in verdicts.assess(
            R.worktrees(), "refs/heads/main", "main", frozenset({"ignored-files"})
        )
    }["holds"]
    assert (v.verdict, v.eligible) == (verdicts.REMOVE, True)


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
        for x in verdicts.assess(R.worktrees(), "refs/heads/main", "main", frozenset())
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
        for x in verdicts.assess(R.worktrees(), "refs/heads/main", "main", frozenset())
    }["feature"]
    assert v.verdict != verdicts.REMOVE, "the tag answered for the branch"


def test_the_head_ref_is_never_a_bare_name(world) -> None:
    """A3. The prefix is needed on the head ref as well as on the branch."""
    world.git("tag", "main", "main")
    assert R.head_ref("").ref == "refs/heads/main"
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    assert R.head_ref("origin").ref == "refs/remotes/origin/main"


def test_every_printed_sha_is_a_commit(world) -> None:
    """A2. An annotated tag's sha gives a restore line with an empty tree."""
    world.worktree("done")
    sha = world.git("rev-parse", "refs/heads/done")
    line = prune.restore_line("done")
    assert line == f"git branch done {sha}"
    assert world.git("cat-file", "-t", sha) == "commit"
    assert prune.restore_line("no-such-branch") == ""


# git rejects a space and a control character in a branch name, and permits
# `$`, a backtick, `;` and `>`. Unquoted, this one redirects: the shell writes
# `pwned` and passes git a branch called `weird`.
HOSTILE = "weird>pwned"


def test_the_restore_line_is_safe_to_paste(world) -> None:
    """The other half of A2. The line exists to be pasted into a shell."""
    world.git("branch", HOSTILE, "main")
    sha = world.git("rev-parse", f"refs/heads/{HOSTILE}")
    line = prune.restore_line(HOSTILE)
    assert line == f"git branch '{HOSTILE}' {sha}"

    world.git("branch", "--delete", "--force", HOSTILE)
    subprocess.run(["bash", "-c", line], cwd=world.repo, check=True)
    assert world.git("rev-parse", f"refs/heads/{HOSTILE}") == sha
    assert not (world.repo / "pwned").exists()


def test_the_switch_hint_is_safe_to_paste(world) -> None:
    """`gwbc` refusing a name it already has prints the same kind of line,
    built from a name somebody typed."""
    from worktrees import new_branch

    world.git("branch", HOSTILE, "main")
    with pytest.raises(new_branch.Refusal) as caught:
        new_branch.create(HOSTILE)
    hint = str(caught.value).split("; ", 1)[1]
    assert hint == f"git switch '{HOSTILE}' checks it out"


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

    rows = verdicts.assess(R.worktrees(), "refs/heads/main", "main", frozenset())
    assert all(v.branch != "main" for v in rows)


def test_a_tag_shadowing_the_head_branch_does_not_shadow_it(world) -> None:
    """B2. `git tag origin/main feature` is the spelling that makes it visible."""
    world.worktree("feature")
    world.commit("f.txt", "f\n", at=world.parent / ".worktrees" / "feature" / "repo")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    world.git("tag", "origin/main", "refs/heads/feature")
    # The trap is live: git resolves the bare name to the tag.
    assert world.git("rev-parse", "origin/main") == world.git(
        "rev-parse", "refs/heads/feature"
    )

    head = R.head_ref("origin").ref
    assert head == "refs/remotes/origin/main"
    v = {x.branch: x for x in verdicts.assess(R.worktrees(), head, "main", frozenset())}
    assert v["feature"].verdict != verdicts.REMOVE


def detached(world, name: str, base: str = "main") -> Path:
    path = world.parent / ".worktrees" / name / "repo"
    path.parent.mkdir(parents=True)
    world.git("worktree", "add", "--quiet", "--detach", str(path), base)
    return path


def judged(world) -> verdicts.Verdict:
    (row,) = verdicts.assess(R.worktrees(), "refs/heads/main", "main", frozenset())
    return row


def test_a_detached_worktree_is_named_not_mistaken(world) -> None:
    """B7. A detached worktree is judged against the head branch, and a
    commit on its first-parent line holds nothing: it is `empty`."""
    detached(world, "detached")
    row = judged(world)
    assert (row.branch, row.label) == ("", "(detached)")
    assert (row.verdict, row.eligible) == (verdicts.EMPTY, False)
    assert row.why == f"{verdicts.EMPTY_WHY}; {verdicts.NEEDS_EMPTY}"


def test_a_stash_is_a_ref_that_reaches_a_detached_commit(world) -> None:
    """The premise: `git for-each-ref --contains` counts refs/stash, so a
    commit made on a detached HEAD and then stashed over reads as reached."""
    wt = detached(world, "dstash")
    sha = world.commit("work", at=wt)
    wt.joinpath("work").write_text("more\n")
    world.git("stash", "--quiet", at=wt)
    assert world.git("for-each-ref", "--format=%(refname)", "--contains", sha) == (
        "refs/stash"
    )
    assert world.git("stash", "list", "--format=%gs").startswith("WIP on (no branch):")


def test_a_detached_commit_only_a_stash_reaches_is_kept(world) -> None:
    wt = detached(world, "dstash")
    world.commit("work", at=wt)
    wt.joinpath("work").write_text("more\n")
    world.git("stash", "--quiet", at=wt)
    assert judged(world).verdict == verdicts.KEEP


def test_another_remote_reaching_a_detached_commit_proves_nothing(world) -> None:
    world.git("remote", "add", "origin", str(world.root / "origin.git"))
    world.git("remote", "add", "up", str(world.root / "up.git"))
    world.git("config", "checkout.defaultRemote", "origin")
    wt = detached(world, "dup")
    sha = world.commit("work", at=wt)
    world.git("update-ref", "refs/remotes/up/x", sha)
    assert world.git("for-each-ref", "--format=%(refname)", "--contains", sha) == (
        "refs/remotes/up/x"
    )
    assert judged(world).verdict == verdicts.KEEP


def test_a_detached_commit_the_head_branch_merged_is_remove(world) -> None:
    world.finished_branch("done")
    detached(world, "dmerged", "done")
    world.git("branch", "-D", "done")
    row = judged(world)
    assert (row.verdict, row.eligible) == (verdicts.REMOVE, True)
    assert row.why == "its tip is an ancestor of the head branch"


# --------------------------------------------------------------------------
# Gate 3: main and master survive whatever the head branch is
# --------------------------------------------------------------------------

RULE = verdicts.MAIN_OR_MASTER


def trunk_head(world) -> None:
    """A synced remote whose HEAD is trunk, so main and master are not it."""
    from test_sync import sync

    world.git("branch", "--move", "main", "trunk")
    origin = world.root / "origin.git"
    world.git("init", "--quiet", "--bare", "-b", "trunk", str(origin))
    world.git("remote", "add", "origin", str(origin))
    world.git("push", "--quiet", "origin", "trunk")
    p = sync(world)
    assert p.returncode == 0, p.stderr


@pytest.mark.parametrize("verdict", ["empty", "remove"])
@pytest.mark.parametrize("name", ["main", "master"])
def test_a_finished_main_or_master_worktree_is_not_eligible(
    world, name, verdict
) -> None:
    from test_cli import run

    trunk_head(world)
    if verdict == "empty":
        # At trunk's tip.
        path = world.worktree(name, base="trunk")
    else:
        # Merged into trunk with a merge commit, and pushed.
        path = world.finished(name)
        world.git("push", "--quiet", "origin", "trunk")
    sha = world.git("rev-parse", f"refs/heads/{name}")

    # No permission makes it eligible.
    p = run(world, "gwp", "--json", "--delete", "all")
    row = next(v for v in json.loads(p.stdout)["verdicts"] if v["branch"] == name)
    assert row["verdict"] == verdict
    assert row["eligible"] is False
    assert row["why"].endswith(f"; {RULE}")

    p = run(world, "gwp", "--yes", "--delete", "all")
    assert p.returncode == 0, p.stderr
    assert path.exists()
    assert world.git("rev-parse", f"refs/heads/{name}") == sha


def test_rule_1_comes_before_ignored_files(world) -> None:
    """Otherwise gwr's consent to ignored files would make master eligible."""
    trunk_head(world)
    path = world.worktree("master", base="trunk")
    (path / ".gitignore").write_text("build/\n")
    world.git("add", ".gitignore", at=path)
    world.git("commit", "--quiet", "-m", "ignore", at=path)
    world.git("checkout", "--quiet", "--detach", at=world.repo)
    world.git("merge", "--quiet", "--ff-only", "master", at=world.repo)
    world.git("push", "--quiet", "origin", "HEAD:trunk")
    world.git("update-ref", "refs/remotes/origin/trunk", "master")
    (path / "build").mkdir()
    (path / "build" / "out").write_text("x")
    head = "refs/remotes/origin/trunk"
    allowed = frozenset({"empty"})
    row = {v.branch: v for v in verdicts.assess(R.worktrees(), head, "trunk", allowed)}
    assert row["master"].verdict == verdicts.EMPTY
    assert row["master"].ignored == 0
    assert row["master"].why.endswith(f"; {RULE}")


@pytest.mark.parametrize("name", ["main", "master"])
def test_gwr_keeps_main_or_master(world, name) -> None:
    from test_cli import run

    trunk_head(world)
    path = world.worktree(name, base="trunk")
    sha = world.git("rev-parse", f"refs/heads/{name}")

    p = run(world, "gwr", name, "--yes")
    assert p.returncode == 1
    assert RULE in p.stderr
    assert path.exists()

    p = run(world, "gwr", name, "--yes", "--keep-branch")
    assert p.returncode == 0, p.stderr
    assert not path.exists()
    assert world.git("rev-parse", f"refs/heads/{name}") == sha


@pytest.mark.parametrize("name", ["main", "master"])
@pytest.mark.parametrize("finished", [True, False], ids=["finished", "unfinished"])
@pytest.mark.parametrize("argv", [(), ("--yes",)], ids=["asked", "yes"])
def test_gwbd_refuses_main_or_master_by_name(world, name, finished, argv) -> None:
    from test_cli import run

    trunk_head(world)
    world.git("branch", name, "trunk")
    if not finished:
        world.git("checkout", "--quiet", name)
        world.commit("more.txt")
        world.git("checkout", "--quiet", "trunk")
    sha = world.git("rev-parse", f"refs/heads/{name}")

    p = run(world, "gwbd", name, *argv)
    assert p.returncode == 3, p.stderr
    assert RULE in p.stderr
    assert world.git("rev-parse", f"refs/heads/{name}") == sha


def test_gwbd_all_keeps_main_and_master(world) -> None:
    from test_cli import run

    trunk_head(world)
    for name in ("main", "master", "done"):
        world.git("branch", name, "trunk")

    p = run(world, "gwbd", "--all", "--yes", "--delete", "all")
    assert p.returncode == 0, p.stderr
    assert world.git("branch", "--list", "--format=%(refname:short)").split() == [
        "main",
        "master",
        "trunk",
    ]


def test_a_sweep_given_master_keeps_the_branch(world) -> None:
    """A row that wrongly says master is eligible removes only the worktree."""
    world.git("branch", "--move", "main", "trunk")
    path = world.worktree("master", base="trunk")
    sha = world.git("rev-parse", "refs/heads/master")
    row = verdicts.Verdict(
        verdicts.REMOVE, "master", str(path), "", sha=sha, eligible=True
    )
    said: list[str] = []
    # The recheck sees what assessment saw, so only branch_delete stands
    # between this forged row and the ref.
    recorded = {row.path: prune.look(row.path, repo=world.repo)}
    removed, failed = prune.sweep(
        [row], recorded, lambda _: row, "trunk", said.append, repo=world.repo
    )
    assert removed == [row] and failed == 1
    assert not path.exists()
    assert world.git("rev-parse", "refs/heads/master") == sha
    assert any(RULE in line for line in said)


@pytest.mark.parametrize(
    ("name", "why"),
    [("main", RULE), ("master", RULE), ("trunk", verdicts.HEAD_BRANCH)],
)
def test_the_branch_delete_itself_refuses(world, name, why) -> None:
    """Below every verdict: nothing reaches update-ref for these names."""
    world.git("branch", "--move", "main", "trunk")
    world.git("checkout", "--quiet", "--detach")
    for other in ("main", "master"):
        world.git("branch", other, "trunk")
    sha = world.git("rev-parse", f"refs/heads/{name}")
    with pytest.raises(Refused) as caught:
        prune.branch_delete(name, sha, "trunk", repo=world.repo)
    assert str(caught.value) == why
    assert world.git("rev-parse", f"refs/heads/{name}") == sha


@pytest.mark.parametrize(
    ("name", "why"),
    [("main", RULE), ("master", RULE), ("trunk", verdicts.HEAD_BRANCH)],
)
def test_gwm_refuses_to_rename_main_master_or_the_head_branch(world, name, why) -> None:
    """A rename takes the old name away as surely as a delete does."""
    from test_cli import run

    trunk_head(world)
    world.git("checkout", "--quiet", "--detach")
    path = world.parent / ".worktrees" / name / world.repo.name
    path.parent.mkdir(parents=True, exist_ok=True)
    if name == "trunk":
        world.git("worktree", "add", "--quiet", str(path), "trunk")
    else:
        world.worktree(name, base="trunk")
    sha = world.git("rev-parse", f"refs/heads/{name}")

    p = run(world, "gwm", "renamed", at=path)
    assert p.returncode == 1, p.stderr
    assert why in p.stderr
    assert path.exists()
    assert world.git("rev-parse", f"refs/heads/{name}") == sha
    assert not world.git("branch", "--list", "renamed")


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

    world.finished("done")
    verdicts.assess(R.worktrees(), "refs/heads/main", "main", frozenset())

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
# one git status per worktree, not two
# --------------------------------------------------------------------------


def test_a_worktree_is_read_once(world) -> None:
    """`--ignored=traditional` is a superset of plain `--porcelain`, so asking
    both ways was half of every assessment's git calls.
    """
    from worktrees.git import options

    for name in ("a", "b", "c"):
        world.worktree(name)
    verdicts.assess(R.worktrees(), "refs/heads/main", "main", frozenset())

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
