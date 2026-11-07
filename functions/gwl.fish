# gwl [QUERY]: gw list, the word already typed.
function gwl --wraps 'gw list' --description 'pick a worktree and cd into it'
    # The gw function rather than `command gw`, so the caller still lands
    # where gw list says.
    gw list $argv
end
