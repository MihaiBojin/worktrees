# Navigation functions change the caller’s directory; shortcuts forward flags.
#
# $PATH is the package's business. `uv tool install` puts the binaries in
# ~/.local/bin; nothing here adds a directory to it.

0="${${ZERO:-${0:#$ZSH_ARGZERO}}:-${(%):-%N}}"
0="${${(M)0:#/*}:-$PWD/$0}"

fpath+=("${0:h}/functions" "${0:h}/completions")
autoload -Uz gw gwa gwl gwm gwr 'gwr!' 'gwp!' 'gwp!!'

# The release this plugin came from, which `gwh` and `gw version` compare
# with their own. Exported, because the environment is all a binary can see
# of the shell that started it.
export WORKTREES_PLUGIN_VERSION=0.7.0
