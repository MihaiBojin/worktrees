---
name: release
description: Release a version of this package to PyPI. Bumps the version on a release branch, opens and merges the pull request once its checks pass, then tags the merged commit so publish.yml uploads it. Use when the user says "release", "cut a release", "ship x.y.z", or "/release x.y.z".
user-invocable: true
allowed-tools: Bash(git:*), Bash(gh:*), Bash(uv:*), Bash(scripts/*.bash:*)
---

# release

Release `$ARGUMENTS` of this package. The argument is the version, with or
without a leading `v`; strip it and use the bare `x.y.z` everywhere below.

With no argument, ask which version. Do not guess one from the commits.

Every step is one command. Run them in order, read what each says before
running the next, and stop at the first failure rather than working around
it. Nothing here is worth improvising: a release that goes wrong is on PyPI
forever.

## 1. Everything that has to be true first

```bash
git fetch --all --prune
scripts/check-releasable.bash <version>
```

That checks the shape of the version, a clean working tree, that the version
is after the one on `origin/main`, that the tag is free on the remote, and
that PyPI does not already carry it. If it refuses, say what it said and
stop. Do not fix a dirty tree by stashing on the user's behalf.

## 2. The branch, the notes, and the bump

```bash
git switch --create release/v<version> --no-track origin/main
```

Then run `/release-notes:draft <version>`, from the ReleaseTools plugin. It
rules on every commit since the previous tag, writes the body to
`.git/RELEASE_EDITMSG`, and puts the entry in `CHANGELOG.md` under
`## <version> - <date>`. It shows the draft and waits before its last step,
so the user changes it there.

Do not write that entry by hand and do not skip it: `publish.yml` reads the
section back out for the GitHub release, and `build` refuses a tag whose
version has no section.

```bash
uv version <version>
git commit --all --message "Release <version>"
git push --set-upstream origin refs/heads/release/v<version>
```

`uv version` rewrites `pyproject.toml` and re-locks, so the commit carries
those two files and `CHANGELOG.md`. Check that with `git show --stat`
before pushing: a commit
missing `uv.lock` fails `uv sync --locked` in CI, and it fails after the
merge rather than before it.

Branch from `origin/main` rather than from `main`, so a stale local copy
cannot become the release.

## 3. The pull request, and its checks

```bash
gh pr create --base main --title "Release <version>" --body "..."
gh pr checks --watch --fail-fast
```

The body is the changelog entry just written. It is already the summary,
already reviewed by the reader, and writing a second one invites the two to
disagree.

`--watch` blocks. When it reports a failure, report which check failed and
its URL, and stop. The branch and the pull request stay; nothing has been
tagged and nothing published.

## 4. Merge, then wait for main

```bash
gh pr merge --rebase --delete-branch
git switch main && git pull --ff-only
scripts/await-checks.bash "$(git rev-parse HEAD)"
```

`--rebase` keeps the commit message rather than replacing it with the pull
request title.

The wait matters. A rebase onto a main that moved is a tree neither the
branch nor main has tested, and the tag must only ever land on a commit
already proved green. `await-checks.bash` blocks until `tests.yml` finishes
on that commit and exits non-zero if it failed.

If it failed: `main` now carries the version bump and no release exists. Say
so plainly. The fix is another pull request, and then this skill again at the
same version, since no tag was created.

## 5. Tag, which is what publishes

```bash
git tag --annotate v<version> --message "v<version>"
git push origin refs/tags/v<version>
gh run watch "$(gh run list --workflow publish.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
```

Pushing the tag is the release. `publish.yml` re-checks the tag against
`pyproject.toml`, that the commit is on `main`, and the tests, then builds
once and uploads to TestPyPI before PyPI.

Report the PyPI URL and the GitHub release URL when it finishes.

## What this never does

Move a tag. A tag names one commit forever; a version that needs a second
attempt gets the next number. If `publish.yml` fails *after* the PyPI upload,
in `verify` or `release`, re-run it with
`gh workflow run publish.yml --ref v<version>` rather than re-tagging.

Delete a release branch that has not merged, stash the user's work, force
anything, or retry a failed check by re-running it in the hope it passes.
