# gwr! [PATH|QUERY]: gwr, also deleting the worktree's ignored files.
function gwr! --wraps 'gwr --delete-ignored' --description 'gwr, deleting the gitignored files too'
    # The gwr function rather than `command gwr`, so the caller still lands
    # where gwr says.
    gwr --delete-ignored $argv
end
