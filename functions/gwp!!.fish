# gwp!!: gwp --yes that also deletes
# empty branches, ignored files and branch stashes.
function gwp!! --wraps 'gwp --delete all --yes' --description 'gwp without asking, deleting empty branches, ignored files and branch stashes'
    gwp --delete all --yes $argv
end
