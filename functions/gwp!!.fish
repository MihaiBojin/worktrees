# gwp!! deletes empty branches, ignored files and branch stashes.
function gwp!! --wraps 'gwp --delete all --yes' --description 'delete empty branches, ignored files and branch stashes'
    gwp --delete all --yes $argv
end
