# worktrees branch status

`worktrees branch status [NAME ...]`, `gw branch status [NAME ...]`, and
`gwbs [NAME ...]` assess local branches. With no names they assess every
local branch. Names match exactly; multiple names retain the supplied order.
An unknown name stops the command with exit 2.

```console
gwbs
gwbs feature old-fix
worktrees branch status --json
```

The head branch and branches checked out in any worktree are `keep`. The
report includes the worktree paths. Branches without worktrees are assessed
against a pinned commit on the repository's
[head branch](../../README.md#the-head-branch), which the command names on
stderr first, as in `Head: origin/main (remote)`. When the head branch does
not resolve, every branch is `unknown` and the command exits 1.

`gws` judges the branch of each worktree with these same checks, after its
own worktree protections, so a worktree and a loose branch at the same
commit read the same verdict and the same `why`.

## Verdicts and evidence

| Verdict | Meaning |
| --- | --- |
| `remove` | A check proves the branch's work is in the head branch. |
| `keep` | A protection applies, or the available checks do not prove the work is finished. |
| `unknown` | History or merge evidence is insufficient to decide. |

The checks run in this order and stop at the first proof:

| `proof` | Check |
| --- | --- |
| `forge` | The last `gw sync` recorded a merged GitHub PR or GitLab MR for this source tip, with a merge commit contained in the head branch. |
| `ancestry` | The branch tip is an ancestor of the head commit. |
| `squash` | The trees match, or the branch's combined patch matches a patch in the head branch. |
| `content` | Every path changed from the common ancestor to the branch tip has the same final state in the head commit. |

The content check includes file modes and object IDs. Deleted paths must be
absent in both trees. Renames are compared as deletion plus addition. Changes
to unrelated paths on the head branch do not prevent a match. The check needs
one merge base; unrelated histories remain `unknown` unless another check
provides proof.

For example, a branch adds `parser.py`. The head branch imports the identical
file in a commit that also changes `README.md`. Individual patch IDs and the
full trees differ, but the content check proves the branch's final change is
present. A branch does not need an upstream or a prior push for this proof.

Different content after later edits is not a content match. Historical
ancestry or squash evidence can still prove that the work was merged.

## GitHub and GitLab

Assessment never runs `gh` or `glab`. It reads the evidence the last
[`gw sync`](../../README.md#gw-sync) recorded in `git-worktrees-sync.json`,
one entry per local branch, keyed by the tip that was asked about. At sync,
`gh pr list` or `glab mr list` requests the newest request for each source
branch, explicitly in the default remote's repository. `GH_REPO` and
`GITLAB_REPO` cannot redirect the query.

A forge proof requires all of these facts:

- The sync file names the default remote and its current URL.
- The entry was recorded at the branch's current tip.
- The request is `MERGED` and has a merge timestamp.
- The source repository and branch match, and the source SHA equals the
  local branch tip being assessed.
- The target branch is this repository's head branch.
- The reported merge commit, or GitLab squash commit when no merge commit
  is reported, exists locally and is an ancestor of the pinned head commit.

A missing remote branch is not evidence of a merge. Reusing a branch name
after an earlier PR or MR does not make its new commits removable. A missing
SHA or a mismatched repository prevents forge proof. Requests from forks
require independent git proof. A branch moved since the last sync, and a
sync file that is missing or malformed, give no forge evidence; git alone
decides.

A `CLOSED` request without a merge never grants permission to delete. Its
branch can still qualify through git evidence, including identical changed
paths when the work was committed through another branch.

GitHub queries request `headRefOid`, `headRefName`, `headRepository`,
`baseRefName`, `mergeCommit`, and `mergedAt`. GitLab queries read `sha`,
`source_branch`, `source_project_id`, `target_project_id`, `target_branch`,
`merge_commit_sha`, `squash_commit_sha`, and `merged_at`. See the
[GitHub CLI reference](https://cli.github.com/manual/gh_pr_list) and
[GitLab merge request API](https://docs.gitlab.com/api/merge_requests/).

Hostnames `github.com` and `github.*` select `gh`; `gitlab.com` and
`gitlab.*` select `glab`. Other hosts use git evidence only. `gw sync` needs
the selected CLI installed and authenticated. Missing tools, failed requests,
and malformed responses record no evidence, so they cannot authorize
deletion.

## Flags and output

| Flag | Behaviour |
| --- | --- |
| `--json` | Print structured evidence on stdout. Diagnostics use stderr. |
| `-q`, `--quiet` | Omit the summary, and the `Head:` line unless the head branch was inferred. |
| `-v`, `--verbose` | Print executed commands on stderr. |
| `--explain` | Print the declared git commands and safety rules, then exit. |

A run reaches no remote. It judges from the refs already here.

JSON contains `head`, `head_sha`, and `verdicts`. Each verdict has `branch`,
`sha`, `verdict`, `why`, `proof`, `paths`, `request`, `error`, and `undo`.
`request` is null when the last sync recorded no usable request for this tip,
or a git check proved the branch. Otherwise it records the request number,
URL, state, repository, source branch and SHA, target branch, merge SHA, and
merge timestamp.

Exit 0 means assessment completed, including `keep` and `unknown` results.
Exit 1 means a git operation failed. Exit 2 means invalid usage. Ctrl-C exits
130.

Use [branch delete](branch-delete.md) to act on the removable branches.
