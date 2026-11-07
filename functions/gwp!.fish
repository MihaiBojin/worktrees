# gwp!: gwp without the question; it grants no --delete permission.
function gwp! --wraps 'gwp --yes' --description 'gwp, removing every eligible worktree without asking'
    gwp --yes $argv
end
