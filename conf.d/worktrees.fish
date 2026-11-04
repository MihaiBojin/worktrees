# Fisher autoloads functions/ and completions/ on demand, and the binaries
# come from `uv tool install` rather than from this package.
#
# The release this plugin came from, which `gwh` and `gw version` compare
# with their own. Exported, because the environment is all a binary can see
# of the shell that started it.
set -gx WORKTREES_PLUGIN_VERSION 0.6.2
