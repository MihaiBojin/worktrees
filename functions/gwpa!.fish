# gwpa!: gwpa without the question.
function gwpa! --wraps 'gwpa --yes' --description 'gwpa without asking'
    gwpa --yes $argv
end
