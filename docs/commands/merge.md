# Squash GitHub pull requests

`gw merge` squashes GitHub pull requests after checking their head SHAs and
CI, through GitHub's asynchronous merge API. A pull request in a native
stack merges with every open pull request below it, in one call. The
repository comes from the default remote; `GH_REPO` does not redirect it.

```console
gw merge 42 --match-head-commit FULL_SHA --subject 'Fix parser (#42)' --body-file message.txt --json
gw merge 41 42 43 --json
```

With one `NUMBER`, `--match-head-commit` is required. An omitted
`--subject` or `--body-file` uses the corresponding field gw builds below.
An explicit subject is used as given, including its `(#NUMBER)` suffix.
`--body-file` reads UTF-8 text unchanged, including trailing newlines;
that file supplies the trailers the commit needs.

With several numbers, gw reads each pull request's head and CI and builds
each message itself, so `--match-head-commit`, `--subject` and
`--body-file` are a usage error at exit 2.

The generated subject is the title and ` (#NUMBER)`. The generated body
is the pull request's body without the
`🤖 Generated with` footer line, a bare `https://claude.ai/code/session_...`
line and the `<details><summary>Auto-resolution</summary>` block, keeping
any `release-note` block, followed by each of `pr.trailers` from
[`gw ci-checks`](ci-checks.md) that the body does not already hold. A pull
request whose `pr.trailers` is null needs an explicit `--body-file`: its
commit list was incomplete, so gw refuses to build its body.

The command always requests squash and accepts no strategy flag. A direct
squash receives the subject and body; a merge queue controls its own
message and strategy. It submits with or without `--json`; that flag prints
the outcome as data. `-v` prints the forge calls. It refuses under
`WORKTREES_OFFLINE`, changes no checkout and deletes no branch.

## Checks before submission

The command reads the pull request, its CI through `gw ci-checks`'s
snapshot, and, in a native stack, the ordered stack-members endpoint. It
refuses missing or inconsistent membership.

The named pull request needs `checksState: passed` or no checks at all, an
open review that is ready, and merge state `CLEAN`, `HAS_HOOKS` or
`UNSTABLE`. A repository without CI has nothing to wait for, so an empty
check list passes.

Every open member below it in its stack is read the same way, at the head
the stack reported for it, and must pass the same checks. A member that
fails one refuses the merge at exit 3 and names that member. A member below
that is closed without merging refuses too.

When members at the bottom of the stack have merged already, the lowest open
member must have settled from that merge: its base must be the stack's base
and its head must contain the merge commit of the most recently merged
member below. Members tied for that merge time each need containment. Each
commit is read from `pulls/{member}` using the default API version, which
exposes `merge_commit_sha`, and `compare/{merge}...{head}` must report
`behind_by: 0`. The command polls every five seconds for up to ten minutes
for those two; a timeout refuses at exit 3 and names the condition unmet.

A pull request in no native stack whose base is not the default branch is
checked for a base left behind. GitHub moves a stacked pull request's base
only when the merged branch below it is deleted. When the base branch
stands exactly at the head commit of a merged pull request from it, this
one would merge into a finished branch and never reach the default branch,
so the command refuses at exit 3 and names the remedy: `gw fast-forward` in
the branch's checkout, which moves the base and pushes the branch. A base
that has moved on since its merge, or that no pull request merged, is
merged into as asked. A read that fails refuses.

Once the stack settles, a named head different from `--match-head-commit`
returns `head-moved` at exit 4 with the new SHA in `sha`. Nothing is
submitted. Run `gw ci-checks NUMBER --wait --json`, require that SHA and
passed checks in a fresh snapshot, and rebuild the message with its
non-null `pr.trailers` before retrying. The ship-it skill retries once per
pull request and stops on a second movement.

The command reads membership and containment again immediately before
submission and refuses any change between those reads.

## Stacks

GitHub's asynchronous merge of a stacked pull request "includes all open
downstack pull requests", atomically: the group merges, is queued, or none
of it does. `gw merge 42` on the middle of 41, 42 and 43 merges 41 and 42;
43 stays open, and GitHub rewrites its branch onto the result.

The `sha` the command sends guards only the named pull request: the API
takes one head SHA and has no field for the members below it. gw reads each
lower member's head and CI before submitting and refuses when one is not
ready. After a `merged` answer it reads each lower member again and warns,
without failing, when one is not merged or merged at a head other than the
one it checked: the merge has already happened. Between gw's last read and
GitHub running the merge, a push to a lower member can still land in the
merge unchecked. No API call closes that gap.

GitHub builds each lower member's squash message from the repository's
`squash_merge_commit_title` and `squash_merge_commit_message` settings, not
from the subject and body the command sends, which reach the named pull
request only. When a merge carries lower members and those settings are not
`PR_TITLE` and `PR_BODY`, the command warns that their squash commits will
use GitHub's default message. `gw preflight` checks those settings, as
its `squash-defaults` check, and prints the `gh api` command that sets
them.

Several numbers merge in the order given, bottom first, one call each, and
the command reads the next pull request again after each merge. Numbers in
the same native stack take one call, on the member highest in the stack,
placed where the first of them is in the order; that call also merges any
open member below it that was not named. The first call that does not
answer `merged`, a refusal included, stops the run: nothing after it is
submitted. A refusal there exits 3 with the JSON still printed, so the
merges before it are reported.

After a merge that leaves members open above it, the command fetches the
default remote and looks at each of their branches that exists here:

| `result` | branch | what happens |
| --- | --- | --- |
| `moved` | stood at the head GitHub had before the merge | moved to the fetched rewritten copy with `git update-ref`, which refuses when the branch moved meanwhile |
| `checked-out` | checked out in a worktree | left where it is; run `gw fast-forward` in that worktree, which takes the rewritten copy |
| `kept` | stands elsewhere, such as at a commit the remote lacks | left where it is |
| `not-rewritten` | GitHub has not rewritten the remote copy yet | left where it is |

A branch already at the fetched copy, or on a fork, is left out.

GitHub's `gh stack` extension also merges stacked pull requests. This
command makes the request itself, `PUT pulls/NUMBER/merge-async`, because
`gh stack merge`:

- takes no commit subject or body, so each squash gets the message the
  repository's settings make, not the `(#NUMBER)` subject and trailers this
  command is given;
- takes no head SHA from its caller, so it merges the heads current when it
  submits, not the ones whose CI and message the caller checked, where this
  command sends `sha` and GitHub refuses a head that differs from it;
- prints nothing a program can read back, where this command prints the
  outcome under `--json`.

It also needs the extension installed, and this command needs only `gh`.

## Outcomes

The command submits once per call with `merge_method=squash` and
`merge_action=default`, which uses a queue where the repository configures
one. It provides no rule-bypass option.

| `status` | exit | meaning |
| --- | --- | --- |
| `merged` | 0 | GitHub reports the merge commit in `sha`. |
| `enqueued` | 0 | GitHub accepted the PR into its merge queue. |
| `pending` | 1 | Background processing has not finished within the polling window. |
| `failed` | 1 | GitHub refused the operation; `message` gives its reason. |
| `head-moved` | 4 | The settled stack head differs from the supplied SHA; `sha` holds the new head and nothing was submitted. |
| `refused` | 3 | With several numbers only: gw refused this pull request before submitting; `message` gives the reason. |

With one number, JSON carries `status`, `pr`, `sha`, `uuid`, `message`,
`merged`, `remaining`, `branches`, `warnings`, `head` and `lastSync`.
`merged` lists each pull request the call merged as `number` and `sha`,
its merge commit, members below included. `remaining` lists each open pull
request left in the stack, or the ones not merged, as `number`, `headSha`,
`baseRef`, `mergeStateStatus` and `checksState`, read after the call.
`branches` lists the local branches the table above describes, as `number`,
`branch`, `result`, `from`, `to` and `path`. `warnings` repeats what stderr
warned of.

With several numbers, JSON carries `status`, the last call's; `results`,
one object a call with the fields above and its `number` and `head`;
`merged`, every call's together; `remaining`, what the last call left and
every number after it; and `head` and `lastSync`. Those two describe local
sync evidence, not the forge reads. The exit code is the last call's.

Without `--json`, each call prints `#NUMBER: STATUS SHA` on stdout. When the
calls merged more pull requests than they name, or left any open, a table
follows with one row for each, its state, SHA, base, merge state and checks.

Pending operations are polled every two seconds for one minute. Each forge
call has a 30-second timeout, so reads can extend either polling window.
An `enqueued` answer ends polling; it does not mean the PR merged.

HTTP 400 with `status: failed` is a failed outcome at exit 1, including when
`gh` exits nonzero. `details.message` supplies its reason. HTTP 409 identifies
an existing operation. The command refuses to adopt it because GitHub
does not echo that operation's commit message, so its trailers cannot be
verified.

With one number, a refusal exits 3 and prints its reason on stderr. Invalid
arguments exit 2. Unknown statuses, malformed responses and network failures
never cause a second submission. After a pending result or uncertain
failure, the merge may still complete. Read the PR before retrying:

```console
gh pr view 42 --json state,mergeCommit,mergedAt
```

Only `MERGED` confirms completion. An open PR may still have a merge in
progress. GitHub retains operation results for 24 hours after their latest
update. The [API reference](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request-asynchronously)
describes the request and status fields.
