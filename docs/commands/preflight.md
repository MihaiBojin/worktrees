# Preflight

`gw preflight` checks whether gw and its agent plugin are set up in the
repository it runs in. It reads; it writes no file, no ref and no forge
setting, and it never syncs first. Run it once in each repository, and
`/worktrees:preflight project` to write the project files it reports
missing.

```console
gw preflight          # one line a check, with its fix
gw preflight --json   # every check as data
```

Each check has a status: `ok`, `missing`, `wrong` or `skipped`. A check that
is `missing` or `wrong` carries its fix: a command to paste, or a file and
the lines it needs. Exit 0 means every check that ran is `ok`; exit 1 means
at least one is `missing` or `wrong`. Outside a repository it exits 3.

| check | ok when | skipped when |
| --- | --- | --- |
| `forge-cli` | `gh auth status --hostname HOST` succeeds for a GitHub remote, `glab auth status` for a GitLab one | no default remote, a remote on another host, or `WORKTREES_OFFLINE` once the CLI is found installed |
| `squash-defaults` | GitHub's `allow_squash_merge` is true, `squash_merge_commit_title` is `PR_TITLE` and `squash_merge_commit_message` is `PR_BODY` | no GitHub remote, `gh` not signed in, `WORKTREES_OFFLINE`, or a repository whose settings GitHub does not show to this account |
| `claude-code` | the top level's `.claude/settings.json` declares the `mihaibojin` marketplace and sets `worktrees@mihaibojin` to true | never |
| `codex` | the top level's `.codex/config.toml` declares the `mihaibojin` marketplace and enables `worktrees@mihaibojin` | never |
| `shell-plugin` | `WORKTREES_PLUGIN_VERSION` is the release of these commands | never |

## Squash defaults

`gw merge` merges a native stack's lower pull requests in one call, and
GitHub writes each one's squash commit from the repository's settings. With
the title set to `PR_TITLE` and the message to `PR_BODY`, each pull
request's reviewed title and body become its commit. The fix is a command
for the repository's admin to run:

```console
gh api -X PATCH repos/OWNER/REPO -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=PR_BODY
```

It adds `-F allow_squash_merge=true` when squash merging is off, and
`--hostname HOST` for a GitHub Enterprise host. gw reads these settings with
`gh api repos/OWNER/REPO` and never writes them.

## Claude Code

```json
{
  "extraKnownMarketplaces": {
    "mihaibojin": {
      "source": {
        "source": "github",
        "repo": "MihaiBojin/agent-plugins"
      }
    }
  },
  "enabledPlugins": {
    "worktrees@mihaibojin": true
  }
}
```

[Claude Code's settings reference](https://code.claude.com/docs/en/settings-reference#extraknownmarketplaces)
names both keys. `additionalMarketplaces`, the alias it documents, counts as
`extraKnownMarketplaces`. Claude Code reads the marketplace only after a
person trusts the folder, and
[loads a plugin the project enables](https://code.claude.com/docs/en/plugins/loading#enabled-in-project-settings-but-not-installed)
only once it is installed on that machine:

```console
claude plugin install worktrees@mihaibojin --scope project
```

## Codex

```toml
[marketplaces.mihaibojin]
source_type = "git"
source = "https://github.com/MihaiBojin/agent-plugins.git"

[plugins."worktrees@mihaibojin"]
enabled = true
```

Codex reads a project's `.codex/config.toml` only in a trusted project, and
does not clone a marketplace declared there:
`codex plugin marketplace upgrade mihaibojin` clones it and installs the
plugins the project enables.

## Shell plugin

The fish and zsh plugins export `WORKTREES_PLUGIN_VERSION`. The check
answers for the shell `gw preflight` runs in, as `gw version` does: an
agent's shell inherits the variable from the shell that started its client.

## JSON

```json
{
  "checks": [
    {"name": "forge-cli", "status": "ok", "detail": "gh is signed in to github.com", "fix": null}
  ],
  "lastSync": {"refs": null, "forge": null},
  "head": {"ref": "refs/remotes/origin/main", "source": "remote"}
}
```

The checks come in the order above. `fix` is null for `ok` and `skipped`.
