# Subcommands and worktrees come from the CLI. `gw --complete` prints every
# name and shorthand with its description, and `gw list --complete` every
# worktree, so a command added to the table needs no edit here. Both are
# offered first, since `gw QUERY` is `gw list QUERY`.
#
# Only the flags the subcommands share are offered, and --json not after
# sync, which takes none. The rest are on the binaries, where
# they are typed: repeating the per-command sets here would be a second copy
# of nine files that has to change with them.

complete -c gw -f
complete -c gw -n __fish_use_subcommand -a '(command gw --complete)'
complete -c gw -n __fish_use_subcommand -a '(command gw list --complete 2>/dev/null)'

complete -c gw -n '__fish_seen_subcommand_from branch; and not __fish_seen_subcommand_from create status delete' \
    -a '(command gw branch --complete)'
complete -c gw -n '__fish_seen_subcommand_from branch; and __fish_seen_subcommand_from status delete' \
    -a '(command gwbs --complete)'
complete -c gw -n '__fish_seen_subcommand_from branch; and __fish_seen_subcommand_from status' \
    -l only -x -a 'remove keep unknown' -d 'show only rows with this verdict'
complete -c gw -n '__fish_seen_subcommand_from branch; and __fish_seen_subcommand_from delete' \
    -l all -d 'assess all local branches for deletion'
complete -c gw -n '__fish_seen_subcommand_from branch; and __fish_seen_subcommand_from delete' \
    -l dry-run -d 'report without deleting'
complete -c gw -n '__fish_seen_subcommand_from branch; and __fish_seen_subcommand_from delete' \
    -s y -l yes -d 'skip deletion confirmation'

complete -c gw -n '__fish_seen_subcommand_from list' \
    -a '(command gw list --complete 2>/dev/null)'
complete -c gw -n '__fish_use_subcommand; or __fish_seen_subcommand_from list' \
    -s l -l list -d 'print them all and pick none'
complete -c gw -n '__fish_seen_subcommand_from remove rm' \
    -a '(command gwr --complete)'

complete -c gw -n 'not __fish_seen_subcommand_from sync' -l json -d 'the result as data'
complete -c gw -s q -l quiet -d 'less output'
complete -c gw -s v -l verbose -d 'print every git command'
complete -c gw -l explain -d 'print every git command and exit'

complete -c gw -s h -l help -d 'show this help message and exit'
