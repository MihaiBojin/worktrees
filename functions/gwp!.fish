# gwp! skips confirmation when removing eligible worktrees.
function gwp! --wraps 'gwp --yes' --description 'skip confirmation for eligible worktrees'
    gwp --yes $argv
end
