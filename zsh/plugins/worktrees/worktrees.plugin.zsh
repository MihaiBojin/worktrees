# gw changes the caller's directory; each shortcut calls gw with fixed words.
#
# $PATH is the package's business. `uv tool install` puts gw in
# ~/.local/bin; nothing here adds a directory to it.

0="${${ZERO:-${0:#$ZSH_ARGZERO}}:-${(%):-%N}}"
0="${${(M)0:#/*}:-$PWD/$0}"

fpath+=("${0:h}/functions" "${0:h}/completions")
autoload -Uz gw gwa gwbc gwbp gwbs gwff gwl gwm gwp 'gwp!' gwpa 'gwpa!' gwr 'gwr!' gws

# The release this plugin came from, which `gw --help` and `gw version` compare
# with their own. Exported, because the environment is all a binary can see
# of the shell that started it.
export WORKTREES_PLUGIN_VERSION=0.8.0
