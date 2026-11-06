# Delegate flags and values to the command's own completion.
function __worktrees_complete_gw
    set -l tokens (commandline -opc)
    set -l args $tokens[3..]
    set -l target
    switch "$tokens[2]"
        case ''
            set target __worktrees_list
        case sync version v
            return
        case prune p
            set target gwp
        case add a
            set target gwa
        case list
            set target __worktrees_list
        case move m mv
            set target gwm
        case remove rm
            set target gwr
        case rotate rot
            set target gwrot
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
        case '*'
            set target __worktrees_list
            set args $tokens[2..]
    end
    test -n "$target"; or return
    set -l line (string join ' ' -- $target (string escape -- $args))
    set -l current (commandline -ct)
    complete -C "$line $current"
end

complete -c gw -f -a '(__worktrees_complete_gw)'
complete -c gw -n 'contains -- (commandline -opc)[2] version v' -s h -l help -d 'show the help and exit'
complete -c gw -n 'test (count (commandline -opc)) -eq 1' -a '(command gw --complete)'
complete -c gw -n 'test (count (commandline -opc)) -eq 1' -a '(command gw list --complete)'
complete -c gw -n 'contains -- sync (commandline -opc)[2]' -l json -d 'sync state as data'
complete -c gw -n 'contains -- sync (commandline -opc)[2]' -s q -l quiet -d 'omit the summary'
complete -c gw -n 'contains -- sync (commandline -opc)[2]' -s v -l verbose -d 'print every command'
complete -c gw -n 'contains -- sync (commandline -opc)[2]' -s h -l help -d 'show the help and exit'

# Listing has no standalone binary; keep its completion local to gw.
complete -c __worktrees_list -f -a '(command gw list --complete)'
complete -c __worktrees_list -s l -l list -d 'print them all and pick none'
complete -c __worktrees_list -l json -d 'the result as data'
complete -c __worktrees_list -l explain -d 'print every git command and exit'
complete -c __worktrees_list -s h -l help -d 'show the help and exit'
