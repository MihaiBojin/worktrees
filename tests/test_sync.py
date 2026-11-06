"""`gw sync`: one fetch of the default remote, and the file that records it."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import env_for

NAME = "git-worktrees-sync.json"


def sync(world, *args: str, at: Path | None = None, **env: str):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gw; raise SystemExit(gw())",
            "sync",
            *args,
        ],
        cwd=str(at or world.repo),
        capture_output=True,
        text=True,
        env={**env_for(world), **env},
    )


def read(world, at: Path | None = None):
    """What the reader returns, asked from inside the repository."""
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from worktrees import sync; print(json.dumps(sync.read()))",
        ],
        cwd=str(at or world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
        check=True,
    )
    return json.loads(p.stdout)


def bare(world, name: str = "origin") -> Path:
    """A bare repository on disk holding main, added as remote `name`."""
    path = world.root / f"{name}.git"
    world.git("init", "--quiet", "--bare", "-b", "main", str(path))
    world.git("remote", "add", name, str(path))
    world.git("push", "--quiet", name, "main")
    return path


def common(world) -> Path:
    return world.repo / ".git"


def leftovers(world) -> list[Path]:
    return [p for p in common(world).iterdir() if p.name.startswith(f".{NAME}.")]


def test_sync_fetches_sets_head_and_records_the_remote(world) -> None:
    path = bare(world)
    # Nine hours east of UTC, so a local time would be off by more than the
    # few seconds allowed below.
    p = sync(world, TZ="Asia/Tokyo")
    assert p.returncode == 0, p.stderr
    assert p.stdout == ""
    assert p.stderr.strip() == "synced origin; head is origin/main"
    assert (
        world.git("symbolic-ref", "refs/remotes/origin/HEAD")
        == "refs/remotes/origin/main"
    )
    record = json.loads((common(world) / NAME).read_text())
    assert record["remote"] == {"name": "origin", "url": str(path).removesuffix(".git")}
    assert record["forge"] is None
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["refs"])
    refs = datetime.strptime(record["refs"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert abs((datetime.now(UTC) - refs).total_seconds()) < 60
    assert read(world) == record
    assert leftovers(world) == []


def test_quiet_says_nothing_and_verbose_traces_git(world) -> None:
    bare(world)
    assert sync(world, "-q").stderr == ""
    p = sync(world, "-v")
    assert re.search(r"^\+ git .*fetch --prune origin$", p.stderr, re.M), p.stderr
    assert re.search(r"^\+ git .*remote set-head origin --auto$", p.stderr, re.M)


def test_an_unreachable_remote_fails_and_leaves_the_file(world) -> None:
    bare(world)
    assert sync(world).returncode == 0
    before = (common(world) / NAME).read_text()
    world.git("remote", "set-url", "origin", str(world.root / "gone.git"))
    p = sync(world, "-q")
    assert p.returncode == 1
    assert "origin could not be synced" in p.stderr
    assert "gone.git" in p.stderr  # git's own reason
    assert (common(world) / NAME).read_text() == before
    assert leftovers(world) == []


def test_a_remote_with_no_branch_fails_and_writes_nothing(world) -> None:
    empty = world.root / "empty.git"
    world.git("init", "--quiet", "--bare", "-b", "main", str(empty))
    world.git("remote", "add", "origin", str(empty))
    p = sync(world)
    assert p.returncode == 1
    assert "origin could not be synced" in p.stderr
    assert "Cannot determine remote HEAD" in p.stderr  # git's own reason
    assert not (common(world) / NAME).exists()


def test_no_remote_is_a_note_and_writes_nothing(world) -> None:
    p = sync(world)
    assert p.returncode == 0, p.stderr
    assert "no remote" in p.stderr
    assert not (common(world) / NAME).exists()
    assert read(world) is None


def test_several_remotes_without_the_setting_refuse(world) -> None:
    bare(world, "origin")
    bare(world, "upstream")
    p = sync(world)
    assert p.returncode == 1
    assert "origin and upstream" in p.stderr
    assert "git config checkout.defaultRemote <name>" in p.stderr
    assert "fetch" not in sync(world, "-v").stderr
    assert not (common(world) / NAME).exists()


def test_a_default_remote_that_is_not_a_remote_is_named(world) -> None:
    bare(world, "origin")
    bare(world, "upstream")
    world.git("config", "checkout.defaultRemote", "nope")
    p = sync(world)
    assert p.returncode == 1
    assert "checkout.defaultRemote names nope, which is none of them" in p.stderr
    assert not (common(world) / NAME).exists()


def test_several_remotes_with_the_setting_sync_that_one(world) -> None:
    bare(world, "origin")
    bare(world, "upstream")
    world.git("config", "checkout.defaultRemote", "upstream")
    p = sync(world, "-v")
    assert p.returncode == 0, p.stderr
    assert "fetch --prune upstream" in p.stderr
    assert "fetch --prune origin" not in p.stderr
    assert read(world)["remote"]["name"] == "upstream"


def test_the_file_is_shared_by_every_worktree(world) -> None:
    bare(world)
    linked = world.worktree("feature")
    p = sync(world, at=linked)
    assert p.returncode == 0, p.stderr
    assert (common(world) / NAME).is_file()
    assert read(world) == read(world, at=linked) is not None


def test_a_malformed_file_reads_as_never_synced_and_is_replaced(world) -> None:
    path = bare(world)
    # The right remote, so these are refused for their types and not for it.
    remote = {"name": "origin", "url": str(path).removesuffix(".git")}
    for junk in (
        "{",
        "[]",
        '{"refs": 3}',
        '{"refs": "x", "forge": null}',
        json.dumps({"remote": remote, "refs": 3, "forge": None}),
        json.dumps({"remote": remote, "refs": "x", "forge": 3}),
    ):
        (common(world) / NAME).write_text(junk)
        assert read(world) is None, junk
    (common(world) / NAME).write_text(
        json.dumps({"remote": remote, "refs": "x", "forge": None})
    )
    assert read(world) is not None
    assert sync(world).returncode == 0
    assert read(world) is not None


def test_a_changed_remote_url_reads_as_never_synced(world) -> None:
    bare(world)
    assert sync(world).returncode == 0
    other = world.root / "elsewhere.git"
    world.git("clone", "--quiet", "--bare", str(world.root / "origin.git"), str(other))
    world.git("remote", "set-url", "origin", str(other))
    assert read(world) is None
    assert sync(world).returncode == 0
    assert read(world)["remote"]["url"] == str(other).removesuffix(".git")


def test_a_renamed_remote_reads_as_never_synced(world) -> None:
    bare(world)
    assert sync(world).returncode == 0
    world.git("remote", "rename", "origin", "upstream")
    assert read(world) is None


def test_sync_help_says_what_it_does(world) -> None:
    p = sync(world, "--help")
    assert p.returncode == 0
    assert "fetch the default remote and ask the forge" in p.stdout + p.stderr


def test_sync_takes_no_json_and_the_help_says_so(world) -> None:
    assert sync(world, "--json").returncode == 2
    listed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gw; raise SystemExit(gw())",
            "-h",
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env_for(world),
    )
    assert "gw sync takes no --json" in listed.stderr


# --------------------------------------------------------------------------
# what the forge says about each branch tip
# --------------------------------------------------------------------------

FAKES = {"github": ("gh", "--head"), "gitlab": ("glab", "--source-branch")}


def forge_remote(world, provider: str = "github") -> None:
    """A remote whose URL names the forge and whose fetch reaches a bare
    repository on disk."""
    path = world.root / "origin.git"
    world.git("init", "--quiet", "--bare", "-b", "main", str(path))
    url = f"git@{provider}.com:owner/repo.git"
    world.git("config", f"url.{path}.insteadOf", url)
    world.git("remote", "add", "origin", url)
    world.git("push", "--quiet", "origin", "main")


def fake(world, provider: str, answers: dict[str, str], *, code: int = 0):
    """A forge CLI that prints `answers[branch]` for the branch it is asked
    about, and `[]` for any other. A branch whose answer is `fail` exits 1
    with a reason on stderr. `code` makes every question fail with it."""
    tool, flag = FAKES[provider]
    bin_dir = world.root / f"{tool}-bin"
    bin_dir.mkdir(exist_ok=True)
    cases = "".join(
        f"  *' {flag} {name} '*) echo 'boom for {name}' >&2; exit 1;;\n"
        if answer == "fail"
        else f"  *' {flag} {name} '*) cat <<'JSON'\n{answer}\nJSON\n  exit 0;;\n"
        for name, answer in answers.items()
    )
    body = (
        f"echo 'not logged in' >&2\nexit {code}\n"
        if code
        else f'case " $* " in\n{cases}esac\necho "[]"\n'
    )
    exe = bin_dir / tool
    exe.write_text(f"#!/bin/sh\n{body}")
    exe.chmod(0o755)
    env = env_for(world)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return env


def synced(world, env, *args: str):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from worktrees.cli import gw; raise SystemExit(gw())",
            "sync",
            *args,
        ],
        cwd=str(world.repo),
        capture_output=True,
        text=True,
        env=env,
    )


def record(world) -> dict:
    return json.loads((common(world) / NAME).read_text())


def a_request(world, provider: str, branch: str) -> str:
    from test_branch_forge import evidence

    row = evidence(world, provider)
    tip = world.git("rev-parse", branch)
    key = (
        ("headRefName", "headRefOid")
        if provider == "github"
        else ("source_branch", "sha")
    )
    row.update({key[0]: branch, key[1]: tip})
    return json.dumps([row])


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_every_branch_tip_is_recorded_with_its_answer(world, provider) -> None:
    forge_remote(world, provider)
    world.git("branch", "feature")
    world.commit("feature", "f\n", at=world.repo)  # main moves on
    world.git("branch", "spike")
    wt = world.worktree("checked-out")
    world.commit("c.txt", "c\n", at=wt)
    env = fake(world, provider, {"feature": a_request(world, provider, "feature")})

    p = synced(world, env)
    assert p.returncode == 0, p.stderr
    file = record(world)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", file["forge"])
    assert sorted(file["evidence"]) == ["checked-out", "feature", "main", "spike"]
    for name, entry in file["evidence"].items():
        assert entry["sha"] == world.git("rev-parse", name), name
    request = file["evidence"]["feature"]["request"]
    assert request["number"] == 17
    assert request["source_branch"] == "feature"
    assert request["source_sha"] == world.git("rev-parse", "feature")
    assert request["target_branch"] == "main"
    assert request["same_repository"] is True
    assert set(request) >= {"state", "url", "repository", "merge_sha", "merged_at"}
    assert file["evidence"]["spike"]["request"] is None  # no request
    assert file["evidence"]["checked-out"]["request"] is None
    assert read(world) == file


def test_a_moved_branch_is_recorded_at_its_new_tip(world) -> None:
    forge_remote(world)
    world.git("branch", "feature")
    env = fake(world, "github", {})
    assert synced(world, env).returncode == 0
    before = record(world)["evidence"]["feature"]["sha"]
    world.git("checkout", "--quiet", "feature")
    world.commit("f.txt", "f\n", at=world.repo)
    world.git("branch", "-q", "gone", "main")
    assert synced(world, env).returncode == 0
    world.git("branch", "-q", "-d", "gone")
    assert synced(world, env).returncode == 0
    after = record(world)["evidence"]
    assert after["feature"]["sha"] == world.git("rev-parse", "feature") != before
    assert "gone" not in after


def test_a_host_that_is_not_a_forge_records_no_forge(world) -> None:
    bare(world)
    world.git("branch", "feature")
    p = sync(world)
    assert p.returncode == 0, p.stderr
    file = record(world)
    assert file["forge"] is None
    assert file["evidence"] == {}
    assert file["refs"] is not None


def _seeded(world, provider: str = "github") -> dict:
    """A sync whose forge answered, so a failure after it has something to
    keep."""
    forge_remote(world, provider)
    world.git("branch", "feature")
    world.git("branch", "spike")
    env = fake(world, provider, {"feature": a_request(world, provider, "feature")})
    assert synced(world, env).returncode == 0
    return record(world)


def test_a_missing_forge_cli_keeps_the_last_evidence(world) -> None:
    before = _seeded(world)
    time.sleep(1.1)
    only_git = world.root / "git-bin"
    only_git.mkdir()
    (only_git / "git").symlink_to(shutil.which("git"))
    env = {**env_for(world), "PATH": f"{only_git}:/usr/bin:/bin"}
    p = synced(world, env)
    assert p.returncode == 1
    assert "gh is not installed" in p.stderr
    after = record(world)
    assert after["refs"] > before["refs"]
    assert (after["forge"], after["evidence"]) == (before["forge"], before["evidence"])


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_a_signed_out_forge_cli_keeps_the_last_evidence(world, provider) -> None:
    before = _seeded(world, provider)
    time.sleep(1.1)
    p = synced(world, fake(world, provider, {}, code=4))
    assert p.returncode == 1
    for name in ("feature", "main", "spike"):
        assert f"the forge did not answer for {name}: not logged in" in p.stderr
    after = record(world)
    assert after["refs"] > before["refs"]
    assert (after["forge"], after["evidence"]) == (before["forge"], before["evidence"])


def test_a_branch_the_forge_fails_on_has_no_evidence(world) -> None:
    before = _seeded(world)
    time.sleep(1.1)
    env = fake(
        world,
        "github",
        {"feature": "fail", "spike": a_request(world, "github", "spike")},
    )
    p = synced(world, env)
    assert p.returncode == 1
    assert "the forge did not answer for feature: boom for feature" in p.stderr
    assert "spike" not in p.stderr
    after = record(world)
    assert after["refs"] > before["refs"]
    assert after["forge"] == before["forge"]
    assert sorted(after["evidence"]) == ["main", "spike"]
    assert after["evidence"]["spike"]["request"]["source_branch"] == "spike"


def test_a_failed_fetch_leaves_the_evidence(world) -> None:
    before = _seeded(world)
    url = "git@github.com:owner/repo.git"
    world.git("config", "--unset", f"url.{world.root / 'origin.git'}.insteadOf")
    world.git("config", f"url.{world.root / 'gone.git'}.insteadOf", url)
    marker = world.root / "asked"
    env = fake(world, "github", {})
    (world.root / "gh-bin" / "gh").write_text(f"#!/bin/sh\n: > {marker}\necho '[]'\n")
    p = synced(world, env)
    assert p.returncode == 1
    assert record(world) == before
    assert not marker.exists(), "the forge was asked after a failed fetch"


@pytest.mark.parametrize(
    ("source", "target"), [(True, True), (1, True), (7, 7.0), (None, None)]
)
def test_gitlab_requires_integer_project_ids(world, source, target) -> None:
    forge_remote(world, "gitlab")
    world.git("branch", "feature")
    row = json.loads(a_request(world, "gitlab", "feature"))[0]
    row.update(source_project_id=source, target_project_id=target)
    p = synced(world, fake(world, "gitlab", {"feature": json.dumps([row])}))
    assert p.returncode == 0, p.stderr
    assert record(world)["evidence"]["feature"]["request"]["same_repository"] is False


@pytest.mark.parametrize("payload", ["null", "{}", "[null]", '[{"number": "invalid"}]'])
def test_a_malformed_answer_records_no_evidence(world, payload) -> None:
    forge_remote(world)
    world.git("branch", "feature")
    p = synced(world, fake(world, "github", {"feature": payload}))
    assert p.returncode == 1
    assert "the forge did not answer for feature" in p.stderr
    assert "feature" not in record(world)["evidence"]
