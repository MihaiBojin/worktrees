# CI checks

`gw ci-checks [NUMBER] --json` reads one GitHub pull request or GitLab merge
request and its checks. Without a number it reads the current branch's
review. It changes no refs or sync cache. `--wait` reads until checks settle,
up to 30 minutes; run it in the background where the host supports that.

The snapshot's `pr.headSha` identifies the commit the checks describe.
`checksState` is `passed`, `failed`, `pending`, `blocked` or `unknown`.
`stale: true` means the review moved during the read and forces `unknown`.
Read again before acting on it.

## Commit trailers

`pr.trailers` is an array of strings. Git parses each commit message with
`git interpret-trailers --parse`, which unfolds continuation lines. The
array keeps each full trailer once, in commit order, oldest first. Different
values of the same key stay separate, including coauthors and session IDs.

An empty array means the commits have no trailers. Null means the complete
list is unavailable. Missing or malformed commit data and a list that does
not reach the review's head produce null. A merge message requires a
non-null list from a fresh snapshot whose head matches the passed checks.

GitHub reads `commits` and `statusCheckRollup` in the same `gh pr view` call
as `headSha`. That command returns at most 100 commits without a total
count. A list of 100 therefore produces null trailers, including a review
with exactly 100 commits; a truncated list is never presented as complete.

GitLab reads the MR's commits API across every page before reading the MR
again. A changed review or pipeline makes the snapshot stale and clears
the trailers. Commit messages come from the forge; no local fetch or
commit range is needed.

Exit 0 means a snapshot was read, including one with failing checks or null
trailers. Exit 1 means the review or its checks could not be read. Exit 3
means `WORKTREES_OFFLINE` is set and nothing was read.
