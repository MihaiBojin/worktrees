# gwp! confirms removal of eligible worktrees.
function gwp! --wraps 'gwp --yes' --description 'remove eligible worktrees without asking'
    gwp --yes $argv
end
