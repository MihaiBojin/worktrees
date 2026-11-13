# Squash one GitHub pull request

`gw merge` squashes one pull request after checking its head SHA and CI. It
uses GitHub's asynchronous merge API for ordinary and native stacked PRs.
The repository comes from the default remote; `GH_REPO` does not redirect it.

```console
gw merge 42 --match-head-commit FULL_SHA --subject 'Fix parser (#42)' --body-file message.txt --json
```

`NUMBER` and `--match-head-commit` are required. The command always requests
squash and accepts no strategy flag. `--body-file` reads UTF-8 text, including
trailing newlines. Prepare the subject with its `(#NUMBER)` suffix and the
body with the trailers the commit needs. A direct squash receives those
values; a merge queue controls its own message and strategy.

The command submits with or without `--json`; that flag prints the outcome
as data. `-v` prints the forge calls. The command changes no checkout and
deletes no branch. It refuses under `WORKTREES_OFFLINE` and does not sync
local refs.

## Checks before submission

A native stack is read through GitHub's ordered stack-members endpoint.
The command refuses missing or inconsistent membership. It polls every five
seconds for up to ten minutes until every lower PR is closed with a merge
time, the target's base is the stack's base, and the target head contains the
merge commit of the most recently merged lower PR. Parents tied for the
latest merge time each need containment. Each commit is read from
`pulls/{parent}` using the default API version, which exposes
`merge_commit_sha`. `compare/{merge}...{head}` must report `behind_by: 0`.
A timeout refuses at exit 3 and names the condition still unmet.

Once the stack settles, a head different from `--match-head-commit` returns
`head-moved` at exit 4 with the new SHA in `sha`. Nothing is submitted. Fetch
that head and the remote base, wait for CI on the new SHA, and rebuild the
message and trailers from their merge base before retrying. The ship-it skill
retries once per PR and stops on a second movement.

An unchanged head needs fresh checks with `checksState: passed`, an open
review that is ready, and merge state `CLEAN`, `HAS_HOOKS` or `UNSTABLE`.
The command reads membership and containment again immediately before
submission and refuses changes between those reads. This containment check
blocks submission during GitHub's retarget-before-rewrite interval. The API
has no stack-membership lock; concurrent changes after the final read remain
outside this check. The supplied SHA alone does not stop GitHub from merging
a head it rewrites while an asynchronous request is pending.

Ship named reviews bottom first, reading the next review and its CI after
each merge. A queued or pending parent keeps its children waiting.

## Outcomes

The command submits once with `merge_method=squash` and
`merge_action=default`, which uses a queue where the repository configures
one. It provides no rule-bypass option.

| `status` | exit | meaning |
| --- | --- | --- |
| `merged` | 0 | GitHub reports the merge commit in `sha`. |
| `enqueued` | 0 | GitHub accepted the PR into its merge queue. |
| `pending` | 1 | Background processing has not finished within the polling window. |
| `failed` | 1 | GitHub refused the operation; `message` gives its reason. |
| `head-moved` | 4 | The settled stack head differs from the supplied SHA; `sha` holds the new head and nothing was submitted. |

JSON also carries `pr`, `uuid`, `message`, `head` and `lastSync`. The latter
two describe local sync evidence, not the fresh forge reads.

Pending operations are polled every two seconds for one minute. Each forge
call has a 30-second timeout, so reads can extend either polling window.
An `enqueued` answer ends polling; it does not mean the PR merged.

HTTP 400 with `status: failed` is a failed outcome at exit 1, including when
`gh` exits nonzero. `details.message` supplies its reason. HTTP 409 identifies
an existing operation. The command follows it only when its expected head,
method, queue action and rule-bypass setting match. A supplied subject or
body prevents adoption because GitHub does not echo the existing operation's
commit message.

A refusal exits 3 and prints its reason on stderr. Invalid arguments exit 2.
Unknown statuses, malformed responses and network failures never cause a
second submission. After a pending result or uncertain failure, the merge
may still complete. Read the PR before retrying:

```console
gh pr view 42 --json state,mergeCommit,mergedAt
```

Only `MERGED` confirms completion. An open PR may still have a merge in
progress. GitHub retains operation results for 24 hours after their latest
update. The [API reference](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request-asynchronously)
describes the request and status fields.
