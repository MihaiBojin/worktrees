# Link pull requests into a stack

`gw stack-link` links open GitHub pull requests into a native stack, bottom
first, through GitHub's stacks API. The repository comes from the default
remote; `GH_REPO` does not redirect it.

```console
gw stack-link 41 42 43 --json
```

Each pull request targets the branch of the one before it, and the first
targets the default branch. When none of them is in a stack, the command
creates one, `POST stacks`. When the first ones are the top of a stack, it
adds the rest above them, `POST stacks/NUMBER/add`. When all of them already
stand in one stack in that order, it changes nothing and exits 0, whether or
not some have merged since.

It takes two numbers or more. `-v` prints the forge calls. The command
changes no checkout and pushes nothing. It refuses under `WORKTREES_OFFLINE`
and does not sync local refs.

## Refusals

A refusal exits 3 and prints its reason on stderr. Invalid arguments, one
number alone or a number given twice, exit 2. The command refuses:

- a remote that is not on GitHub, with the words `gw merge` uses:
  `gw stack-link supports GitHub pull requests`;
- a pull request that is closed or merged;
- a pull request whose base is not the head branch of the one before it;
- a first pull request whose base is not the default branch, when the
  command would create the stack;
- a pull request in a stack other than the first one's, or in a stack
  above a pull request that is in none;
- first pull requests that are in a stack but end below its top, since
  GitHub adds pull requests only on top;
- an answer GitHub refuses, such as 422 for pull requests that cannot form
  a stack or 409 while another request changes it, with GitHub's message;
- a stack GitHub returns that does not hold the numbers in the order given.

## JSON

```json
{
  "stack": 5,
  "pull_requests": [41, 42, 43],
  "created": false,
  "added": [43]
}
```

`stack` is the stack's number and `pull_requests` every pull request in it,
bottom first, as GitHub returned them. `created` says whether this run made
the stack, and `added` lists the numbers it linked, empty when nothing
changed. It also carries `head` and `lastSync`, which describe local sync evidence,
as every JSON result does. Without `--json` the command prints one line, such as
`stack 5: #41 #42 #43 (added #43)`.

The [API reference](https://docs.github.com/en/rest/pulls/stacks) describes
both requests.

## Why not `gh stack link`

GitHub's `gh stack` extension also links pull requests into a stack. It
needs `gh extension install github/gh-stack`, and this command needs only
`gh`. It makes the requests itself, as `gw merge` does, and prints what it
did under `--json`.
