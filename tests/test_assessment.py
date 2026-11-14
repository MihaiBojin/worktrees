"""Assessment keeps evidence separate from deletion protections."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from test_cli import run


def rows(world, entry="gw prune", *flags, at=None) -> list[dict]:
    result = run(world, entry, "--json", *flags, at=at)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["verdicts"]


def finished(world, name="done") -> Path:
    wt = world.worktree(name)
    world.commit("finished", at=wt)
    world.git("merge", "--quiet", "--no-ff", "-m", "merge", name)
    return wt


@pytest.mark.parametrize(
    "history",
    [
        "new",
        "old",
        "fast-forward",
        "merge",
        "squash",
        "content",
        "unfinished",
        "unrelated",
    ],
)
def test_worktree_and_loose_branch_share_proof(world, history):
    world.git("branch", "feature")
    if history == "old":
        world.commit("later")
    elif history != "new":
        world.git("switch", "--quiet", "feature")
        if history == "unrelated":
            world.git("checkout", "--orphan", "other")
            world.commit("README", "unrelated")
            world.git("branch", "-f", "feature", "other")
        else:
            world.commit("feature")
        world.git("switch", "--quiet", "main")
        if history in ("fast-forward", "merge"):
            world.git(
                "merge",
                "--quiet",
                "--ff-only" if history == "fast-forward" else "--no-ff",
                "-m",
                "merged",
                "feature",
            )
        elif history in ("squash", "content"):
            world.git("merge", "--quiet", "--squash", "feature")
            world.git("commit", "--quiet", "-m", "squashed change")
            if history == "content":
                world.commit("unrelated")
    want = {
        "new": "empty",
        "old": "empty",
        "fast-forward": "empty",
        "merge": "remove",
        "squash": "remove",
        "content": "remove",
        "unfinished": "keep",
        "unrelated": "unknown",
    }[history]
    loose = {r["branch"]: r for r in rows(world, "gw branch status")}["feature"]
    wt = world.parent / ".worktrees" / "feature" / "repo"
    wt.parent.mkdir(parents=True)
    world.git("worktree", "add", "--quiet", str(wt), "feature")
    attached = {r["branch"]: r for r in rows(world)}["feature"]
    assert loose["verdict"] == attached["verdict"] == want
    assert loose["proof"] == attached["proof"]
    assert loose["sha"] == attached["sha"]
    assert loose["eligible"] == attached["eligible"] == (want == "remove")
    assert "feature" not in {r["branch"] for r in rows(world, "gw branch status")}


def test_divergent_local_main_is_not_the_assessed_first_parent_history(world):
    base = world.git("rev-parse", "main")
    world.git("remote", "add", "origin", "https://example.test/repo.git")
    world.git("update-ref", "refs/remotes/origin/trunk", base)
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    world.commit("local")
    world.worktree("feature")
    row = {r["branch"]: r for r in rows(world)}["feature"]
    assert row["verdict"] == "keep"
    assert not row["eligible"]


@pytest.mark.parametrize("detached", [False, True])
@pytest.mark.parametrize("protection", ["locked", "tracked", "untracked", "current"])
def test_finished_worktree_protections_preserve_verdict(world, protection, detached):
    wt = finished(world)
    if detached:
        world.git("checkout", "--quiet", "--detach", at=wt)
    if protection == "locked":
        world.git("worktree", "lock", str(wt))
    elif protection == "tracked":
        (wt / "README").write_text("changed")
    elif protection == "untracked":
        (wt / "untracked").write_text("keep")
    result = rows(world, at=wt if protection == "current" else None)
    row = next(r for r in result if r["path"] == str(wt))
    # Changes the head branch does not hold are work to keep, whatever the
    # branch's own commits prove.
    dirty = protection in ("tracked", "untracked")
    assert row["verdict"] == ("keep" if dirty else "remove")
    if dirty:
        assert row["why"] == "it has uncommitted changes or untracked files"
    assert not row["eligible"]
    assert row["protections"]
    assert wt.exists()


@pytest.mark.parametrize("detached", [False, True])
def test_initialized_submodule_blocks_finished_worktree(world, detached):
    module = world.root / "module"
    world.git("init", "--quiet", "-b", "main", str(module))
    world.commit("README", at=module)
    wt = world.worktree("done")
    world.git(
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "--quiet",
        str(module),
        "module",
        at=wt,
    )
    world.git("commit", "--quiet", "-m", "module", at=wt)
    world.git("merge", "--quiet", "--no-ff", "-m", "merge", "done")
    if detached:
        world.git("checkout", "--quiet", "--detach", at=wt)
    row = next(r for r in rows(world) if r["path"] == str(wt))
    assert row["verdict"] == "remove"
    assert not row["eligible"]
    assert "git submodule deinit --all" in row["why"]
    assert (wt / "module" / "README").exists()


def test_a_submodule_store_left_by_deinit_blocks_removal_until_deleted(world):
    """`git worktree remove` refuses while the store exists, so gwp must too."""
    module = world.root / "module"
    world.git("init", "--quiet", "-b", "main", str(module))
    world.commit("README", at=module)
    wt = world.worktree("done")
    world.git(
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "--quiet",
        str(module),
        "module",
        at=wt,
    )
    world.git("commit", "--quiet", "-m", "module", at=wt)
    world.git("merge", "--quiet", "--no-ff", "-m", "merge", "done")
    world.git("submodule", "deinit", "--quiet", "--all", "--force", at=wt)
    store = world.git(
        "rev-parse", "--path-format=absolute", "--git-path", "modules", at=wt
    )
    row = next(r for r in rows(world) if r["path"] == str(wt))
    assert not row["eligible"]
    assert f"then delete {store}" in row["why"]
    shutil.rmtree(store)
    result = run(world, "gw prune", "--yes")
    assert result.returncode == 0, result.stderr
    assert not wt.exists()


def test_a_locked_worktree_whose_directory_is_away_is_kept_and_assessed(world):
    away = world.worktree("away")
    world.git("worktree", "lock", str(away))
    away.rename(away.with_name("elsewhere"))
    done = world.merged_worktree("done")
    found = {r["branch"]: r for r in rows(world)}
    assert found["away"]["verdict"] == "empty"
    assert not found["away"]["eligible"]
    assert "it is locked" in found["away"]["protections"]
    assert "its directory is missing" in found["away"]["protections"]
    assert found["done"]["eligible"]
    result = run(world, "gw prune", "--yes")
    assert result.returncode == 0, result.stderr
    assert not done.exists()
    assert "branch refs/heads/away\nlocked" in world.git(
        "worktree", "list", "--porcelain"
    )


@pytest.mark.parametrize("name", ["main", "master", "trunk"])
def test_protected_branch_names_with_nonconventional_remote_head(world, name):
    world.git("branch", "master")
    world.git("branch", "trunk")
    world.git("switch", "--quiet", "-c", "holding")
    world.git("remote", "add", "origin", "https://example.test/repo.git")
    world.git("update-ref", "refs/remotes/origin/trunk", "trunk")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    loose = {r["branch"]: r for r in rows(world, "gw branch status")}[name]
    assert loose["verdict"] == "empty"
    assert not loose["eligible"]
    assert "branch" in loose["why"]
    wt = world.root / f"protected-{name}"
    world.git("worktree", "add", "--quiet", str(wt), name)
    row = next(r for r in rows(world) if r["path"] == str(wt))
    assert not row["eligible"]
    assert "branch" in row["why"]


@pytest.mark.parametrize("message", [False, True])
@pytest.mark.parametrize("change", ["none", "rename", "rebase", "detach"])
def test_stash_ownership_requires_subject_and_first_parent(
    world, monkeypatch, message, change
):
    wt = finished(world)
    if change == "detach":
        world.git("checkout", "--quiet", "--detach", at=wt)
    (wt / "README").write_text("stash this")
    world.git("stash", "push", *(["-m", "saved"] if message else []), at=wt)
    stash = world.git("rev-parse", "refs/stash")
    if change == "rename":
        world.git("branch", "-m", "done", "renamed")
    elif change == "rebase":
        monkeypatch.setenv("GIT_COMMITTER_DATE", "@1700000100 +0000")
        world.git(
            "rebase",
            "--quiet",
            "--force-rebase",
            "--onto",
            "main~1",
            "main~1",
            "done",
            at=wt,
        )
    row = next(r for r in rows(world) if r["path"] == str(wt))
    assert row["stashes"] == ([stash] if change == "none" else [])
    if change == "none":
        assert row["verdict"] == "remove"
        assert not row["eligible"]
        assert "stash" in row["why"]


@pytest.mark.parametrize("message", [False, True])
def test_prefix_sharing_branches_have_distinct_stashes(world, message):
    paths = {name: world.worktree(name) for name in ("topic", "topic-more")}
    shas = {}
    for name, path in paths.items():
        (path / "README").write_text(name)
        world.git(
            "stash",
            "push",
            *(["-m", "saved 'quoted'; $(data)"] if message else []),
            at=path,
        )
        shas[name] = world.git("rev-parse", "refs/stash")
    attached = {r["branch"]: r for r in rows(world)}
    for name, path in paths.items():
        assert attached[name]["stashes"] == [shas[name]]
        world.git("worktree", "remove", str(path))
    loose = {r["branch"]: r for r in rows(world, "gw branch status")}
    for name in paths:
        assert loose[name]["stashes"] == [shas[name]]


def snapshot(path: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize(
    "entry,flags",
    [
        ("gw prune", ()),
        ("gw prune", ("--table",)),
        ("gw", ("prune",)),
        ("gw", ("prune", "--table")),
    ],
)
def test_preview_preserves_repository_files_refs_stashes_and_stale_records(
    world, entry, flags
):
    world.commit(".gitignore", ".env\n")
    wt = finished(world)
    (wt / ".env").write_text("secret")
    (wt / "README").write_text("saved")
    world.git("stash", "push", "-m", "saved", at=wt)
    stale = world.worktree("gone")
    shutil.rmtree(stale)
    # A divergent branch reaches the synthetic squash probe.
    pending = world.worktree("pending")
    world.commit("pending", at=pending)
    cache = world.repo / ".git" / "git-worktrees-sync.json"
    cache.write_text('{"sentinel": true}\n')
    before = snapshot(world.repo)
    ignored = (wt / ".env").read_bytes()
    records = world.git("worktree", "list", "--porcelain")
    refs = world.git("show-ref")
    stashes = world.git("stash", "list", "--format=%H")
    result = run(world, entry, *flags, "--json")
    assert result.returncode == 0, result.stderr
    assert snapshot(world.repo) == before
    assert world.git("worktree", "list", "--porcelain") == records
    assert world.git("show-ref") == refs
    assert world.git("stash", "list", "--format=%H") == stashes
    assert (wt / ".env").read_bytes() == ignored
    assert str(stale) in json.loads(result.stdout)["stale"]


def test_empty_filter_preserves_ineligible_empty_rows(world):
    world.worktree("empty")
    finished(world)
    result = rows(world, "gw prune", "--only", "empty")
    assert {r["branch"] for r in result} == {"main", "empty"}
    assert all(r["verdict"] == "empty" and not r["eligible"] for r in result)


@pytest.mark.parametrize(
    "args",
    [
        ("prune", "done"),
        ("prune", "--branch", "done"),
    ],
)
def test_retired_status_and_prune_targets_are_usage_errors(world, args):
    wt = finished(world)
    result = run(world, "gw", *args)
    assert result.returncode == 2, result.stderr
    assert wt.exists()


@pytest.mark.parametrize("name", ["status", "s", "st"])
def test_retired_status_names_are_usage_errors_that_point_at_list(world, name):
    target = world.worktree(name)
    result = run(world, "gw", name)
    assert result.returncode == 2
    assert f"gw list {name}" in result.stderr
    result = run(world, "gw", "list", name)
    assert result.returncode == 0, result.stderr
    assert "WORKTREE" in result.stdout and str(target.resolve()) in result.stdout
    assert target.is_dir()


def test_first_parent_history_is_read_once_per_assessment(world):
    from worktrees import cli
    from worktrees.git import options

    for name in ("one", "two", "three"):
        world.worktree(name)
    assert cli.gw(["prune", "--json"]) == 0
    calls = [argv for argv in options.log if "--first-parent" in argv]
    assert len(calls) == 1, calls


def test_validated_forge_precedes_fast_forward_empty(world, network):
    from test_branch_forge import evidence, stub
    from test_branches import cli, local_branch

    local_branch(world)
    world.git("merge", "--quiet", "--ff-only", "feature")
    world.git("remote", "add", "origin", "git@github.com:owner/repo.git")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    env = stub(world, "github", [evidence(world, "github")], network)
    loose = cli(world, "branch", "status", "feature", "--json", env=env)
    assert loose.returncode == 0, loose.stderr
    row = json.loads(loose.stdout)["verdicts"][0]
    assert row["verdict"] == "remove"
    assert row["proof"] == "forge"
    wt = world.root / "feature"
    world.git("worktree", "add", "--quiet", str(wt), "feature")
    preview = cli(world, "prune", "--json", env=env)
    assert preview.returncode == 0, preview.stderr
    attached = next(
        r for r in json.loads(preview.stdout)["verdicts"] if r["branch"] == "feature"
    )
    assert attached["verdict"] == "remove"
    assert attached["proof"] == "forge"
    assert attached["eligible"]


def test_historical_combined_squash_proof_survives_later_head_edit(world):
    from worktrees import merged

    wt = world.worktree("feature")
    world.commit("feature", "first\n", at=wt)
    world.commit("feature", "second\n", at=wt)
    world.git("merge", "--quiet", "--squash", "feature")
    world.git("commit", "--quiet", "-m", "squash")
    world.commit("feature", "later upstream edit\n")
    assert merged.squash_matches("refs/heads/feature", "refs/heads/main")


def test_git_wrapper_standard_input_is_data(world):
    from worktrees.git import git

    @git("hash-object --stdin")
    def hash_input():
        """Hash data without storing an object."""

    value = "$(touch should-not-exist)\n"
    import subprocess

    expected = subprocess.run(
        ["git", "hash-object", "--stdin"],
        input=value,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert hash_input(input=value).out == expected
    assert not (world.repo / "should-not-exist").exists()


@pytest.mark.parametrize("setting", ["diff.context", "environment"])
def test_squash_patch_identity_keeps_surrounding_context(world, monkeypatch, setting):
    from worktrees import merged

    world.commit("file", "a\nb\nc\nold\nd\ne\n")
    wt = world.worktree("feature")
    world.commit("file", "a\nb\nc\nnew\nd\ne\n", at=wt)
    world.commit("file", "x\nb\nc\nold\nd\ne\n")
    world.commit("file", "x\nb\nc\nnew\nd\ne\n")
    if setting == "environment":
        monkeypatch.setenv("GIT_DIFF_OPTS", "--unified=0")
    else:
        world.git("config", setting, "0")
    assert not merged.squash_matches("refs/heads/feature", "refs/heads/main")


@pytest.mark.parametrize(
    "key,value",
    [
        ("diff.ignoreSubmodules", "all"),
        ("diff.submodule", "log"),
        ("diff.submodule", "diff"),
    ],
)
def test_squash_patch_identity_includes_submodule_targets(world, key, value):
    from worktrees import merged

    world.commit("file", "before\n")
    base = world.git("rev-parse", "main")
    wt = world.worktree("feature")
    world.git("update-index", "--add", "--cacheinfo", "160000", base, "module", at=wt)
    world.commit("file", "feature\n", at=wt)
    other = world.commit("other")
    world.git("update-index", "--add", "--cacheinfo", "160000", other, "module")
    world.commit("file", "feature\n")
    world.git("config", key, value)
    assert not merged.squash_matches("refs/heads/feature", "refs/heads/main")


@pytest.mark.parametrize("source", ["gitmodules", "config"])
def test_content_proof_includes_submodules_ignored_by_configuration(world, source):
    from worktrees import merged

    text = '[submodule "module"]\n path = module\n url = https://example.invalid/module.git\n'
    if source == "gitmodules":
        text += " ignore = all\n"
    world.commit(".gitmodules", text)
    if source == "config":
        world.git("config", "submodule.module.ignore", "all")
    world.commit("file", "before\n")
    base = world.git("rev-parse", "main")
    wt = world.worktree("feature")
    world.git("update-index", "--add", "--cacheinfo", "160000", base, "module", at=wt)
    world.commit("file", "feature\n", at=wt)
    other = world.commit("other")
    world.git("update-index", "--add", "--cacheinfo", "160000", other, "module")
    world.commit("file", "feature\n")
    assert not merged.content_matches("refs/heads/feature", "refs/heads/main")


def test_named_checked_out_branch_is_not_reported_as_loose(world):
    wt = finished(world)
    assert rows(world, "gw branch status", "done") == []
    assert wt.exists()


def test_stashes_protect_loose_finished_branches(world):
    wt = finished(world)
    (wt / "README").write_text("saved")
    world.git("stash", "push", "-m", "saved", at=wt)
    stash = world.git("rev-parse", "refs/stash")
    world.git("worktree", "remove", str(wt))
    row = rows(world, "gw branch status", "done")[0]
    assert row["verdict"] == "remove"
    assert row["stashes"] == [stash]
    assert not row["eligible"]
    result = run(world, "gw branch prune", "done", "--yes", "--json")
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)["removed"] == []
    assert world.git("rev-parse", "done") == row["sha"]
    assert world.git("rev-parse", "refs/stash") == stash


@pytest.mark.parametrize("detached", [False, True])
def test_main_checkout_is_never_eligible(world, detached):
    finished(world)
    if detached:
        world.git("checkout", "--quiet", "--detach")
    row = next(r for r in rows(world) if r["path"] == str(world.repo))
    assert not row["eligible"]
    assert "main checkout" in row["why"]


@pytest.mark.parametrize("name", ["main", "master", "trunk"])
def test_protected_branch_deletion_names_rule(world, name):
    world.git("branch", "master")
    world.git("branch", "trunk")
    world.git("remote", "add", "origin", "https://example.test/repo.git")
    world.git("update-ref", "refs/remotes/origin/trunk", "trunk")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    sha = world.git("rev-parse", f"refs/heads/{name}")
    result = run(world, "gw branch prune", name, "--yes", "--json")
    assert result.returncode == 3, result.stderr
    assert "protected branch" in result.stderr
    assert world.git("rev-parse", f"refs/heads/{name}") == sha


@pytest.mark.parametrize(
    "history,want",
    [
        ("unfinished", "unknown"),
        ("fast-forward", "empty"),
        ("merge", "remove"),
        ("squash", "remove"),
    ],
)
def test_invalid_merged_request_preserves_independent_proof(
    world, network, history, want
):
    from test_branch_forge import evidence, stub
    from test_branches import cli, local_branch

    local_branch(world)
    if history in ("fast-forward", "merge"):
        world.git(
            "merge",
            "--quiet",
            "--ff-only" if history == "fast-forward" else "--no-ff",
            "-m",
            "merged",
            "feature",
        )
    elif history == "squash":
        world.git("merge", "--quiet", "--squash", "feature")
        world.git("commit", "--quiet", "-m", "squash")
    world.git("remote", "add", "origin", "git@github.com:owner/repo.git")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    # Off the head's first-parent history, so the target alone invalidates it.
    request = evidence(
        world, "github", baseRefName="other", mergeCommit={"oid": "0" * 40}
    )
    env = stub(world, "github", [request], network)
    result = cli(world, "branch", "status", "feature", "--json", env=env)
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)["verdicts"][0]
    assert row["verdict"] == want
    wt = world.root / "feature"
    world.git("worktree", "add", "--quiet", str(wt), "feature")
    preview = cli(world, "prune", "--json", env=env)
    assert preview.returncode == 0, preview.stderr
    attached = next(
        r for r in json.loads(preview.stdout)["verdicts"] if r["branch"] == "feature"
    )
    assert attached["verdict"] == want
    assert attached["proof"] == row["proof"]


def test_ignored_files_never_prove_an_unfinished_branch(world):
    from test_cli import run_on_a_terminal

    world.commit(".gitignore", ".env\n")
    wt = world.worktree("busy")
    sha = world.commit("unfinished", at=wt)
    (wt / ".env").write_text("secret")
    refused = run(world, "gw remove", "busy", "--yes")
    assert refused.returncode != 0
    assert wt.exists()
    assert world.git("rev-parse", "refs/heads/busy") == sha
    code, output = run_on_a_terminal(
        world,
        "gw remove",
        "busy",
        "--keep-branch",
        answer="y\n",
    )
    assert code == 0, output
    assert not wt.exists()
    assert world.git("rev-parse", "refs/heads/busy") == sha


@pytest.mark.parametrize("entry", ["gw branch status", "gw branch prune", "gw remove"])
def test_first_parent_history_is_reused_through_rechecks(world, entry):
    from worktrees import cli
    from worktrees.git import options

    if entry == "gw remove":
        world.worktree("empty")
        args = ["empty", "--yes"]
    else:
        world.merged_branch("one")
        world.merged_branch("two")
        args = ["--json"] + (
            ["one", "two", "--yes"] if entry == "gw branch prune" else []
        )
    cli.gw([*entry.split()[1:], *args])
    calls = [argv for argv in options.log if "--first-parent" in argv]
    assert len(calls) == 1, calls


@pytest.mark.parametrize(
    "history,want", [("historical", "remove"), ("different-bytes", "keep")]
)
def test_non_utf8_patch_data_preserves_squash_proof(world, history, want):
    world.commit("file", "before\n")
    wt = world.worktree("feature")
    (wt / "file").write_bytes(b"caf\xe9\n")
    world.git("add", "file", at=wt)
    world.git("commit", "--quiet", "-m", "feature", at=wt)
    (world.repo / "file").write_bytes(
        b"caf\xe9\n" if history == "historical" else b"caf\xff\n"
    )
    world.git("add", "file")
    world.git("commit", "--quiet", "-m", "upstream change")
    world.commit("file", "later upstream edit\n")
    world.git("worktree", "remove", str(wt))
    loose = rows(world, "gw branch status", "feature")[0]
    assert loose["verdict"] == want
    if history == "historical":
        assert loose["proof"] == "squash"
    world.git("worktree", "add", "--quiet", str(wt), "feature")
    attached = next(row for row in rows(world) if row["branch"] == "feature")
    assert attached["verdict"] == want
    assert attached["proof"] == loose["proof"]


def test_git_wrapper_round_trips_non_utf8_standard_input(world):
    import subprocess

    from worktrees.git import git

    @git("hash-object --stdin")
    def hash_input():
        """Hash data without storing an object."""

    value = b"caf\xe9\xff\n"
    expected = subprocess.run(
        ["git", "hash-object", "--stdin"], input=value, capture_output=True, check=True
    ).stdout.decode()
    assert (
        hash_input(input=value.decode("utf-8", "surrogateescape")).out.strip()
        == expected.strip()
    )


def test_unrelated_equal_trees_are_unknown_for_loose_and_checked_out_branches(world):
    world.git("checkout", "--quiet", "--orphan", "feature")
    world.git("commit", "--quiet", "-m", "independent root")
    world.git("checkout", "--quiet", "main")
    assert world.git("rev-parse", "feature^{tree}") == world.git(
        "rev-parse", "main^{tree}"
    )
    row = rows(world, "gw branch status", "feature")[0]
    assert row["verdict"] == "unknown"
    assert not row["eligible"]
    assert "no common history" in row["why"]
    wt = world.root / "feature"
    world.git("worktree", "add", "--quiet", str(wt), "feature")
    attached = next(row for row in rows(world) if row["branch"] == "feature")
    assert attached["verdict"] == "unknown"
    assert not attached["eligible"]


@pytest.mark.parametrize("entry,prefix", [("gw prune", ()), ("gw", ("prune",))])
def test_mutating_prune_rejects_only_before_any_changes(world, entry, prefix):
    done = finished(world)
    gone = world.worktree("gone")
    shutil.rmtree(gone)
    before = snapshot(world.repo)
    records = world.git("worktree", "list", "--porcelain")
    refs = world.git("show-ref")
    result = run(world, entry, *prefix, "--only", "empty", "--yes", "--json")
    assert result.returncode == 2, result.stderr
    assert "--only" in result.stderr and "--yes" in result.stderr
    assert done.exists()
    assert snapshot(world.repo) == before
    assert world.git("worktree", "list", "--porcelain") == records
    assert world.git("show-ref") == refs


@pytest.mark.parametrize("name", ["main", "master"])
def test_finished_protected_names_are_ineligible_by_name_alone(world, name):
    side = world.merged_branch("side")
    world.git("branch", "trunk")
    world.git("switch", "--quiet", "-c", "holding")
    world.git("update-ref", "refs/heads/main", side)
    world.git("branch", "master", side)
    world.git("branch", "ordinary", side)
    world.git("remote", "add", "origin", "https://example.test/repo.git")
    world.git("update-ref", "refs/remotes/origin/trunk", "trunk")
    world.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    loose = {row["branch"]: row for row in rows(world, "gw branch status")}
    assert loose["ordinary"]["verdict"] == "remove"
    assert loose["ordinary"]["eligible"]
    assert loose[name]["verdict"] == "remove"
    assert not loose[name]["eligible"]
    assert loose[name]["protections"] == ["it is a protected branch"]
    wt = world.root / name
    world.git("worktree", "add", "--quiet", str(wt), name)
    attached = next(row for row in rows(world) if row["branch"] == name)
    assert attached["verdict"] == "remove"
    assert not attached["eligible"]
    assert attached["protections"] == ["it is a protected branch"]


def test_validated_forge_proof_precedes_unrelated_history(world, network):
    from test_branch_forge import evidence, stub
    from test_branches import cli

    world.git("checkout", "--quiet", "--orphan", "feature")
    world.git("commit", "--quiet", "-m", "independent root")
    world.git("checkout", "--quiet", "main")
    world.git("remote", "add", "origin", "git@github.com:owner/repo.git")
    world.git("update-ref", "refs/remotes/origin/main", "main")
    env = stub(world, "github", [evidence(world, "github")], network)
    result = cli(world, "branch", "status", "feature", "--json", env=env)
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)["verdicts"][0]
    assert row["verdict"] == "remove"
    assert row["proof"] == "forge"


@pytest.mark.parametrize("entry,prefix", [("gw prune", ()), ("gw", ("prune",))])
@pytest.mark.parametrize("mode", [(), ("--json",), ("--table",)])
def test_prune_metadata_preserves_eligible_rows_and_stale_registration(
    world, network, entry, prefix, mode
):
    from test_branches import cli as invoke

    world.commit(".gitignore", "*.secret\n")
    eligible = world.merged_worktree("eligible")
    blocked = world.merged_worktree("blocked")
    (blocked / "kept.secret").write_bytes(b"ignored\nbytes\x00")
    (blocked / "README").write_text("pending")
    world.git("stash", "push", "-qm", "pending", at=blocked)
    gone = world.worktree("gone")
    shutil.rmtree(gone)
    cache = world.repo / ".git" / "git-worktrees-sync.json"
    cache.write_text('{"sentinel": true}\n')
    before = snapshot(world.repo)
    worktrees_before = {str(p): snapshot(p) for p in (eligible, blocked)}
    records = world.git("worktree", "list", "--porcelain")
    refs = world.git("show-ref")
    stashes = world.git("stash", "list", "--format=%H")
    result = invoke(world, *prefix, *mode, entry=entry, env=network.env())
    assert result.returncode == 0, result.stderr
    assert snapshot(world.repo) == before
    assert {str(p): snapshot(p) for p in (eligible, blocked)} == worktrees_before
    assert world.git("worktree", "list", "--porcelain") == records
    assert world.git("show-ref") == refs
    assert world.git("stash", "list", "--format=%H") == stashes
    assert network.network_calls() == []
    assert not any(call[-2:] == ["worktree", "prune"] for call in network.calls())
    assert "git branch eligible " not in result.stderr
    assert "[y/N]" not in result.stderr
    if "--json" in mode:
        data = json.loads(result.stdout)
        row = next(v for v in data["verdicts"] if v["branch"] == "eligible")
        assert row["eligible"] and row["verdict"] == "remove"
        assert str(gone) in data["stale"]
        assert "head" in data and "lastSync" in data
    else:
        assert "VERDICT" in result.stdout and "ELIGIBLE" in result.stdout
        assert str(eligible) in result.stdout and str(blocked) in result.stdout


@pytest.mark.parametrize("mode", [(), ("--json",), ("--table",)])
@pytest.mark.parametrize("permission", [False, True])
def test_prune_yes_approves_metadata_modes_with_category_permissions(
    world, mode, permission
):
    world.commit(".gitignore", "*.secret\n")
    done = world.merged_worktree("done")
    (done / "kept.secret").write_bytes(b"keep until approved\n")
    (done / "README").write_text("pending")
    world.git("stash", "push", "-qm", "pending", at=done)
    tip = world.git("rev-parse", "done")
    result = run(
        world,
        "gw prune",
        *mode,
        "--yes",
        *(["--delete", "stashes"] if permission else []),
    )
    assert result.returncode == 0, result.stderr
    assert "[y/N]" not in result.stderr
    if "--json" in mode:
        data = json.loads(result.stdout)
        row = next(v for v in data["verdicts"] if v["branch"] == "done")
        assert row["verdict"] == "remove" and row["eligible"] == permission
        assert data["removed"] == (["done"] if permission else [])
        assert data["failed"] == 0
        assert "head" in data and "lastSync" in data
    else:
        assert "VERDICT" in result.stdout
    if permission:
        assert not done.exists()
        assert not world.git("branch", "--list", "done")
        assert f"git branch done {tip}" in result.stderr
        assert "  delete ignored   kept.secret, unrecoverable\n" in result.stderr
        assert "git stash store -m" in result.stderr
    else:
        assert (done / "kept.secret").read_bytes() == b"keep until approved\n"
        assert world.git("rev-parse", "done") == tip


def test_keep_branch_does_not_waive_proof_for_a_detached_worktree(world):
    """No branch keeps a detached worktree's commits, so they need proof."""
    wt = world.root / "detached"
    world.git("worktree", "add", "--quiet", "--detach", str(wt))
    tip = world.commit("detached work", at=wt)
    result = run(world, "gw remove", "--keep-branch", "--yes", str(wt))
    assert result.returncode != 0
    assert wt.exists()
    assert world.git("rev-parse", "HEAD", at=wt) == tip


def test_untracked_files_protect_under_show_untracked_files_no(world):
    wt = world.merged_worktree("done")
    (wt / "notes.txt").write_text("precious\n")
    world.git("config", "status.showUntrackedFiles", "no")
    row = next(r for r in rows(world) if r["branch"] == "done")
    assert not row["eligible"]
    assert "it has uncommitted changes or untracked files" in row["protections"]
    result = run(world, "gw prune", "--yes")
    assert result.returncode == 0, result.stderr
    assert (wt / "notes.txt").exists()
