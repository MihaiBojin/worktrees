function gwpa --wraps 'gw prune --delete all' --description 'gw prune --delete all'
    gw prune --delete all $argv
end
