# gwp! [--branch NAME]: gwp after a fresh fetch, deleting ignored files, without asking.
function gwp! --wraps 'gwp --force-refresh --delete-ignored --yes' --description 'gwp, fresh, deleting the gitignored files too, without asking'
    gwp --force-refresh --delete-ignored --yes $argv
end
