# gwp!: gwp, deleting ignored files, without asking.
function gwp! --wraps 'gwp --delete ignored-files --yes' --description 'gwp, deleting the gitignored files too, without asking'
    gwp --delete ignored-files --yes $argv
end
