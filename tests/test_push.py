"""push: the branch you stand on, sent to the default remote."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from conftest import argv, env_for, online


def gw_push(world, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv("gw push", *args),
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env={**online(world), **env},
    )


def upstream(world) -> Path:
    """A bare remote holding main, fetched, with origin/HEAD set."""
    bare = world.root / "upstream.git"
    world.git("init", "--quiet", "--bare", str(bare))
    world.git("remote", "add", "origin", str(bare))
    world.git("push", "--quiet", "origin", "main")
    world.git("fetch", "--quiet", "origin")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return bare


def on_remote(bare: Path, branch: str) -> str:
    found = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", "--verify", "--quiet", branch],
        capture_output=True,
        text=True,
    )
    return found.stdout.strip()


def other_clone(world, bare: Path) -> Path:
    there = world.root / "other"
    world.git("clone", "--quiet", str(bare), str(there))
    return there


def pushed(world, branch: str = "fix-parser") -> None:
    """The branch on the remote and tracked, as `gw push` leaves it."""
    world.git("push", "--quiet", "--set-upstream", "origin", branch)


def raw_push(world, branch: str = "fix-parser") -> subprocess.CompletedProcess:
    """What an agent ran by hand after its own rebase."""
    return subprocess.run(
        ["git", "push", "--force-with-lease", "--force-if-includes", "origin", branch],
        cwd=world.repo,
        capture_output=True,
        text=True,
    )


def test_a_branch_the_remote_lacks_goes_with_set_upstream(world) -> None:
    bare = upstream(world)
    world.git("switch", "--quiet", "--create", "fix-parser")
    tip = world.commit("fix.txt", "w\n")

    p = gw_push(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert {k: payload[k] for k in ("branch", "remote", "pushed", "previous")} == {
        "branch": "fix-parser",
        "remote": "origin",
        "pushed": tip,
        "previous": "",
    }
    assert payload["refused"] == ""
    assert {"head", "lastSync"} <= set(payload)
    assert on_remote(bare, "fix-parser") == tip
    assert world.git("rev-parse", "--abbrev-ref", "fix-parser@{upstream}") == (
        "origin/fix-parser"
    )


def test_a_branch_already_on_the_remote_pushes_nothing(world) -> None:
    upstream(world)
    world.git("switch", "--quiet", "--create", "fix-parser")
    tip = world.commit("fix.txt", "w\n")
    pushed(world)

    p = gw_push(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["pushed"], payload["previous"]) == ("", tip)


def test_a_local_rebase_onto_main_with_new_commits_is_pushed(world) -> None:
    """The ordinary case: rebased here onto a newer main, one commit more."""
    bare = upstream(world)
    world.git("switch", "--quiet", "--create", "fix-parser")
    old = world.commit("fix.txt", "w\n")
    pushed(world)
    there = other_clone(world, bare)
    world.commit("main.txt", "m\n", at=there)
    world.git("push", "--quiet", "origin", "main", at=there)
    world.git("fetch", "--quiet", "origin")
    world.git("rebase", "--quiet", "origin/main")
    tip = world.commit("more.txt", "x\n")

    p = gw_push(world, "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["pushed"], payload["previous"]) == (tip, old)
    assert on_remote(bare, "fix-parser") == tip


def dropped_from_here(world, bare: Path) -> str:
    """Another clone's commit on the branch, fetched, then dropped here by a
    rebase that never saw it go; that commit."""
    world.git("switch", "--quiet", "--create", "fix-parser")
    world.commit("fix.txt", "w\n")
    pushed(world)
    there = other_clone(world, bare)
    world.git("switch", "--quiet", "fix-parser", at=there)
    theirs = world.commit("theirs.txt", "t\n", at=there)
    world.git("push", "--quiet", "origin", "fix-parser", at=there)
    world.git("pull", "--quiet", "--ff-only")
    world.git("rebase", "--quiet", "--onto", f"{theirs}^", theirs)
    world.commit("mine.txt", "m\n")
    return theirs


def test_a_raw_leased_push_deletes_a_commit_the_branch_dropped(world) -> None:
    """The remote tip is in the branch's reflog, so --force-if-includes lets
    the push through, and the other clone's commit is gone."""
    bare = upstream(world)
    theirs = dropped_from_here(world, bare)

    p = raw_push(world)
    assert p.returncode == 0, p.stderr
    gone = subprocess.run(
        [
            "git",
            "--git-dir",
            str(bare),
            "merge-base",
            "--is-ancestor",
            theirs,
            "fix-parser",
        ],
    )
    assert gone.returncode == 1


def test_a_commit_only_the_remote_holds_is_refused(world) -> None:
    bare = upstream(world)
    theirs = dropped_from_here(world, bare)
    before = on_remote(bare, "fix-parser")

    p = gw_push(world, "--json")
    assert p.returncode == 3, p.stderr
    payload = json.loads(p.stdout)
    assert payload["refused"] == "remote-only"
    assert (payload["pushed"], payload["previous"]) == ("", before)
    assert theirs[:7] in p.stderr
    assert "gw fast-forward" in p.stderr
    assert "--replace" in p.stderr
    assert on_remote(bare, "fix-parser") == before


def remote_merge(world, bare: Path, conflict: bool = False) -> str:
    """Another clone merges main into the remote branch."""
    world.git("switch", "--quiet", "--create", "fix-parser")
    world.commit("README" if conflict else "fix.txt", "branch\n")
    pushed(world)
    there = other_clone(world, bare)
    world.commit("README" if conflict else "main.txt", "main\n", at=there)
    world.git("push", "--quiet", "origin", "main", at=there)
    world.git("switch", "--quiet", "fix-parser", at=there)
    merge = subprocess.run(
        ["git", "merge", "--no-ff", "--no-commit", "main"],
        cwd=there,
        capture_output=True,
        text=True,
    )
    assert merge.returncode == (1 if conflict else 0), merge.stderr
    if conflict:
        (there / "README").write_text("remote resolution\n")
        world.git("add", "--", "README", at=there)
    world.git("commit", "--quiet", "-m", "Update branch", at=there)
    merged = world.git("rev-parse", "HEAD", at=there)
    world.git("push", "--quiet", "origin", "fix-parser", at=there)
    return merged


def test_a_raw_leased_push_deletes_a_remote_merge(world) -> None:
    bare = upstream(world)
    merged = remote_merge(world, bare)
    world.git("pull", "--quiet", "--ff-only")
    world.git("rebase", "--quiet", "origin/main")
    tip = world.commit("more.txt", "m\n")

    p = raw_push(world)
    assert p.returncode == 0, p.stderr
    assert on_remote(bare, "fix-parser") == tip
    assert merged not in world.git("rev-list", tip).splitlines()


@pytest.mark.parametrize("conflict", [False, True])
def test_a_merge_only_the_remote_holds_is_refused(world, conflict: bool) -> None:
    bare = upstream(world)
    merged = remote_merge(world, bare, conflict)
    world.commit("more.txt", "m\n")

    p = gw_push(world, "--json")
    assert p.returncode == 3, p.stderr
    payload = json.loads(p.stdout)
    assert payload["refused"] == "remote-only"
    assert (payload["pushed"], payload["previous"]) == ("", merged)
    assert merged[:7] in p.stderr
    assert "Update branch" in p.stderr
    assert on_remote(bare, "fix-parser") == merged


def test_a_remote_merge_kept_in_the_branch_is_pushed(world) -> None:
    bare = upstream(world)
    remote_merge(world, bare, conflict=True)
    world.git("pull", "--quiet", "--ff-only")
    tip = world.commit("more.txt", "m\n")

    p = gw_push(world, "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["pushed"] == tip
    assert on_remote(bare, "fix-parser") == tip


def test_replace_pushes_over_a_remote_merge(world) -> None:
    bare = upstream(world)
    merged = remote_merge(world, bare, conflict=True)
    tip = world.commit("more.txt", "m\n")

    p = gw_push(world, "--replace", "--json")
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert (payload["pushed"], payload["previous"]) == (tip, merged)
    assert on_remote(bare, "fix-parser") == tip


def test_an_empty_commit_only_the_remote_holds_is_refused(world) -> None:
    bare = upstream(world)
    world.git("switch", "--quiet", "--create", "fix-parser")
    world.commit("fix.txt", "w\n")
    pushed(world)
    there = other_clone(world, bare)
    world.git("switch", "--quiet", "fix-parser", at=there)
    world.git("commit", "--quiet", "--allow-empty", "-m", "Trigger CI", at=there)
    empty = world.git("rev-parse", "HEAD", at=there)
    world.git("push", "--quiet", "origin", "fix-parser", at=there)
    world.commit("more.txt", "m\n")

    p = gw_push(world, "--json")
    assert p.returncode == 3, p.stderr
    assert json.loads(p.stdout)["refused"] == "remote-only"
    assert empty[:7] in p.stderr
    assert on_remote(bare, "fix-parser") == empty


def test_a_commit_of_its_own_rewritten_here_is_pushed_over(world) -> None:
    """An amend, or a conflict resolved in a rebase, changes a pushed
    commit's patch-id; the reflog says this clone made the commit."""
    bare = upstream(world)
    world.git("switch", "--quiet", "--create", "fix-parser")
    world.commit("fix.txt", "w\n")
    pushed(world)
    (world.repo / "fix.txt").write_text("better\n")
    world.git("commit", "--quiet", "--all", "--amend", "--no-edit")
    tip = world.git("rev-parse", "HEAD")

    p = gw_push(world, "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["pushed"] == tip
    assert on_remote(bare, "fix-parser") == tip


def test_replace_pushes_over_another_clones_commit_edited_here(world) -> None:
    """A pulled commit amended here reads as one the branch lacks until a
    person says it is replaced."""
    bare = upstream(world)
    world.git("switch", "--quiet", "--create", "fix-parser")
    world.commit("fix.txt", "w\n")
    pushed(world)
    there = other_clone(world, bare)
    world.git("switch", "--quiet", "fix-parser", at=there)
    theirs = world.commit("theirs.txt", "t\n", at=there)
    world.git("push", "--quiet", "origin", "fix-parser", at=there)
    world.git("pull", "--quiet", "--ff-only")
    (world.repo / "theirs.txt").write_text("edited\n")
    world.git("commit", "--quiet", "--all", "--amend", "--no-edit")
    tip = world.git("rev-parse", "HEAD")

    refused = gw_push(world)
    assert refused.returncode == 3
    assert theirs[:7] in refused.stderr
    p = gw_push(world, "--replace", "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["pushed"] == tip
    assert on_remote(bare, "fix-parser") == tip


def stacked_on_a_squash(world, bare: Path) -> str:
    """fix-parser stacked on parser, whose one commit main squashed; the
    parent's commit, still on fix-parser and on its remote copy."""
    world.git("switch", "--quiet", "--create", "parser")
    parent = world.commit("parser.txt", "p\n")
    world.git("switch", "--quiet", "--create", "fix-parser")
    world.commit("fix.txt", "w\n")
    pushed(world)
    there = other_clone(world, bare)
    (there / "parser.txt").write_text("p\n")
    world.git("add", "--", "parser.txt", at=there)
    world.git("commit", "--quiet", "-m", "parser (#1)", at=there)
    world.git("push", "--quiet", "origin", "main", at=there)
    world.git("fetch", "--quiet", "origin")
    world.commit("more.txt", "x\n")
    return parent


def test_a_raw_push_puts_a_merged_parent_back_in_the_request(world) -> None:
    bare = upstream(world)
    parent = stacked_on_a_squash(world, bare)

    p = raw_push(world)
    assert p.returncode == 0, p.stderr
    held = subprocess.run(
        [
            "git",
            "--git-dir",
            str(bare),
            "merge-base",
            "--is-ancestor",
            parent,
            "fix-parser",
        ],
    )
    assert held.returncode == 0
    in_main = subprocess.run(
        ["git", "--git-dir", str(bare), "merge-base", "--is-ancestor", parent, "main"],
    )
    assert in_main.returncode == 1


def test_a_branch_carrying_merged_commits_is_refused_even_with_replace(
    world,
) -> None:
    bare = upstream(world)
    parent = stacked_on_a_squash(world, bare)
    before = on_remote(bare, "fix-parser")

    for extra in ((), ("--replace",)):
        p = gw_push(world, "--json", *extra)
        assert p.returncode == 3, p.stderr
        assert json.loads(p.stdout)["refused"] == "merged"
        assert parent[:7] in p.stderr
        assert "gw fast-forward" in p.stderr
    assert on_remote(bare, "fix-parser") == before


def test_a_rewritten_remote_copy_with_the_same_commits_is_pushed_over(
    world,
) -> None:
    """GitHub's rewrite of a stacked branch holds the branch's own commits by
    patch-id, and the merged parent's only as main's squash."""
    bare = upstream(world)
    stacked_on_a_squash(world, bare)
    world.git("rebase", "--quiet", "--onto", "origin/main", "parser", "fix-parser")
    tip = world.git("rev-parse", "HEAD")

    p = gw_push(world, "--json")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["pushed"] == tip


def test_offline_it_refuses(world) -> None:
    upstream(world)
    world.git("switch", "--quiet", "--create", "fix-parser")
    world.commit("fix.txt", "w\n")
    p = subprocess.run(
        argv("gw push"),
        cwd=world.repo,
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert p.returncode == 3
    assert "WORKTREES_OFFLINE" in p.stderr
    assert p.stdout == ""


def test_the_head_branch_and_a_detached_head_are_refused(world) -> None:
    bare = upstream(world)
    world.commit("main.txt", "m\n")
    assert gw_push(world).returncode == 3
    world.git("switch", "--quiet", "--detach", "HEAD")
    p = gw_push(world)
    assert p.returncode == 3
    assert "detached" in p.stderr
    assert on_remote(bare, "main") != world.git("rev-parse", "HEAD")


def test_without_a_remote_it_refuses(world) -> None:
    world.git("switch", "--quiet", "--create", "fix-parser")
    world.commit("fix.txt", "w\n")
    p = gw_push(world)
    assert p.returncode == 3
    assert "no default remote" in p.stderr
