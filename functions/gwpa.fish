# gwpa: gwp that also deletes
# empty branches, ignored files and branch stashes. It still asks.
function gwpa --wraps 'gwp --delete all' --description 'gwp, also deleting empty branches, ignored files and branch stashes'
    gwp --delete all $argv
end
