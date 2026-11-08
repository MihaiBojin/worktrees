# Pull request format

A pull request's title and body become the squash commit that lands on the
head branch, so they are written once, for a reader a year from now who has
the commit and nothing else. The repository's own rules come first: its
`AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md` and
`.github/pull_request_template.md`, and the convention its recent history
on the head branch follows. What follows applies where they say nothing.

## Title

- What the change does, in the imperative: `Add gw ci-checks`, not `Added gw ci-checks`
  or `gw ci-checks command`.
- The history's prefix convention when it has one, such as Conventional
  Commits' `feat:`, `fix!:` or `docs(readme):`. Mark a breaking change the
  way that convention does.
- Specific enough to tell this change from the next one. `Fix bug` and
  `Update files` say nothing.
- At most 72 characters, no full stop, no `(#NN)`. GitHub appends the number
  when it squashes.

## Body

One line per change a reader can see in the diff, as a list. A change that
needs one line gets one line and no list. For `fix!: Rename branch deletion
to branch prune`:

```
- `gw branch prune` deletes the loose branches proved finished
- the `gbwp` shortcut replaces `gwbd` in fish and zsh
- help, completions and the agent plugin use the new name

Breaking: scripts calling `gw branch delete` must call `gw branch prune`.
```

- Open with one sentence when the list needs a frame: what changes for
  somebody using this, or why, when the diff cannot show it.
- A change is a behaviour, an interface or a decision, not a file and not a
  commit. Three commits fixing one bug are one line; one commit touching
  forty files for one rename is one line.
- `Breaking:` names what a caller must change, in one line of its own.
- A line about testing only when the repository asks for one or something
  was verified that the diff cannot show. Name what ran.
- In a stack, one line naming the pull request below this one.
- Wrap at 72 columns, so the body reads the same as a commit message.

Leave out:

- Anything the diff, the commits or a linked issue does not support. A
  reason nobody gave is invented, however plausible.
- How the work went: `as requested`, `after review`, `this PR`, the
  approaches that were dropped.
- Headings, unless the body is long enough that a reader needs to jump.
  Never `Summary` or `Overview`.
- A restatement of the title, a closing summary, filler and emphasis.

Keep, exactly as written:

- A fenced `release-note` block, at the end, `NONE` included. Two blocks
  are a change that wanted splitting; keep the first and say so.
- Issue keywords such as `Fixes #123`, and the lines a harness or template
  adds, such as a `Generated with` footer or a session link.
- Sections the repository's template requires, filled in.
- An auto-resolution block, below.

## Auto-resolution block

The ship-it skill adds one when it fixed CI on the way to a merge: one row
per problem, with the check, the cause, the fix and the commit that carries
it, above the `release-note` block.

```
<details>
<summary>Auto-resolution</summary>

| check | cause | fix | commit |
| --- | --- | --- | --- |
| `lint` | unused import in `ci.py` | removed it | `3f2a9c1` |

</details>
```

It records how the change got through CI, so it belongs on the review page
and the squash body leaves it out, as it leaves out a `Generated with`
footer.

## What makes a description wrong

Check each line against the diff and the commits:

1. A line claims something the diff does not do, or no longer does after
   later commits.
2. The diff does something a reader would notice that no line mentions: a
   new or renamed flag, command, file format, default, dependency or
   removed behaviour.
3. The title describes a different or smaller change than the diff.
4. A line breaks the rules above or the repository's own.

A description with none of these is right, even when it could be phrased
differently. Rewording a correct line is churn.
