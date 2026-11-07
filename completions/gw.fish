# Delegate flags and values to the command's own completion.
function __worktrees_complete_gw
    set -l tokens (commandline -opc)
    set -l args $tokens[3..]
    set -l target
    switch "$tokens[2]"
        case ''
            set target gwp
            set args --dry-run
        case prune p
            set target gwp
        case add a
            set target gwa
        case list l ls
            set target gwl
        case move m mv
            set target gwm
        case remove rm
            set target gwr
        case rotate rot
            set target gwrot
        case help h
            set target gwh
        case branch
            switch "$tokens[3]"
                case ''
                    command gw branch --complete
                    return
                case create
                    set target gwbc
                case status
                    set target gwbs
                case delete
                    set target gwbd
            end
            set args $tokens[4..]
        case '-*'
            set target gwp
            set args --dry-run $tokens[2..]
    end
    test -n "$target"; or return
    set -l line (string join ' ' -- $target (string escape -- $args))
    set -l current (commandline -ct)
    complete -C "$line $current"
end

complete -c gw -f -a '(__worktrees_complete_gw)'
complete -c gw -n 'test (count (commandline -opc)) -eq 1' -a '(command gw --complete)'
complete -c gw -n 'test (count (commandline -opc)) -eq 1' -a '(command gwl --complete)'
complete -c gw -n 'contains -- sync (commandline -opc)[2]' -l json -d 'sync state as data'
complete -c gw -n 'contains -- sync (commandline -opc)[2]' -s q -l quiet -d 'omit the summary'
complete -c gw -n 'contains -- sync (commandline -opc)[2]' -s v -l verbose -d 'print every command'
complete -c gw -n 'contains -- sync (commandline -opc)[2]' -s h -l help -d 'show the help and exit'
