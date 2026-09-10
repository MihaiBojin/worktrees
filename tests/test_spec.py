"""The spec form: shlex.split runs on the literal spec, before any value."""

from __future__ import annotations

import pytest

from worktrees import new_branch
from worktrees.git import GitError, git, options

# Each of these is a name a shell would act on. shlex.split has already
# finished by the time one is substituted, so each lands as one argv element.
HOSTILE = [
    "feat$(touch /tmp/WORKTREES_PWNED)",
    "feat`touch /tmp/WORKTREES_PWNED`",
    'a"b',
    "a'b",
    "x; rm -rf /tmp/nope",
    "has space",
    "--upload-pack=touch /tmp/WORKTREES_PWNED",
    "$(id)",
]


@pytest.mark.parametrize("name", HOSTILE)
def test_a_hostile_value_is_one_argument(world, name: str, tmp_path) -> None:
    """In a middle position, which is where a quoting bug would show."""
    from worktrees import repo as R

    options.log.clear()
    R.ref_exists(f"refs/heads/{name}")
    argv = options.log[-1]
    assert argv == ("git", "show-ref", "--verify", "--quiet", f"refs/heads/{name}")
    assert not (tmp_path / "WORKTREES_PWNED").exists()


# git accepts these as branch names, which is what makes them the sharper
# test: the tool has to create a branch literally called `$(id)`.
LEGAL = ['a"b', "a'b", "$(id)", "a`id`b"]


@pytest.mark.parametrize("name", LEGAL)
def test_a_branch_name_git_accepts_is_created_verbatim(world, name: str) -> None:
    import pathlib

    started = new_branch.create(name, fetch=False)
    assert started.branch == name
    assert world.git("symbolic-ref", "--short", "HEAD") == name
    assert world.git("rev-parse", "--verify", f"refs/heads/{name}")
    assert not pathlib.Path("/tmp/WORKTREES_PWNED").exists()


@pytest.mark.parametrize("name", ["feat$(touch /tmp/x)", "has space", "x; rm -rf /tmp"])
def test_a_branch_name_git_rejects_stops_at_check_ref_format(world, name) -> None:
    """git says no, and the checkout is untouched."""
    import pathlib

    with pytest.raises(new_branch.Refusal, match="not a valid branch name"):
        new_branch.create(name, fetch=False)
    assert world.git("symbolic-ref", "--short", "HEAD") == "main"
    assert not pathlib.Path("/tmp/WORKTREES_PWNED").exists()


def test_git_revision_braces_survive_a_spec(world) -> None:
    """^{tree} and @{upstream} are git syntax, not placeholders."""
    from worktrees.merged import tree_of

    world.git("branch", "side", "main")
    options.log.clear()
    out = tree_of("refs/heads/side")
    assert options.log[-1] == ("git", "rev-parse", "refs/heads/side^{tree}")
    assert out.out.strip() == world.git("rev-parse", "refs/heads/main^{tree}")


def test_a_value_inside_a_token_stays_one_element(world) -> None:
    @git("for-each-ref --format=$fmt --count=1 refs/heads/")
    def formatted(fmt: str) -> None:
        """A placeholder that is not the whole token."""

    options.log.clear()
    formatted("%(refname:short) %(objectname)")
    assert options.log[-1][-3] == "--format=%(refname:short) %(objectname)"


def test_a_splat_becomes_several_elements(world) -> None:
    @git("show-ref --verify --quiet $*refs", ok=(0, 1))
    def several(refs: list[str]) -> None:
        """A list opened out at its position."""

    options.log.clear()
    several(["refs/heads/main", "refs/heads/other"])
    assert options.log[-1] == (
        "git",
        "show-ref",
        "--verify",
        "--quiet",
        "refs/heads/main",
        "refs/heads/other",
    )


def test_the_body_appends_a_tail(world) -> None:
    @git("log --oneline -1")
    def with_tail(extra: list[str]) -> tuple[str, ...]:
        """Arguments with no fixed position."""
        return tuple(extra)

    options.log.clear()
    with_tail(["--no-decorate"])
    assert options.log[-1] == ("git", "log", "--oneline", "-1", "--no-decorate")


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("log --format=$$H", "--format=$H"),
        ("log --format=100$", "--format=100$"),
        ("log --format=$", "--format=$"),
    ],
)
def test_a_dollar_with_no_name_is_a_literal(world, spec: str, expected: str) -> None:
    """A --format string may hold one. Neither raises KeyError."""

    @git(spec, ok=(0, 128))
    def dollars() -> None:
        """No placeholder here at all."""

    options.log.clear()
    try:
        dollars()
    except GitError:
        pass
    assert options.log[-1][-1] == expected


def test_verbose_prints_a_line_that_pastes(world, capsys) -> None:
    """shlex.join, so a name holding a space comes back quoted."""
    from worktrees import repo as R

    options.verbose = True
    try:
        R.ref_exists("refs/heads/has space")
    finally:
        options.verbose = False
    printed = capsys.readouterr().err
    assert "+ git show-ref --verify --quiet 'refs/heads/has space'" in printed
