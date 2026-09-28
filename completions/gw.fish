# Subcommands come from the CLI. `gw --complete` prints every name and
# shorthand with its description, so a command added to the table needs no
# edit here.
#
# Only the flags every subcommand shares are offered after one. The rest are
# on the binaries, where they are typed: repeating the per-command sets here
# would be a second copy of nine files that has to change with them.

complete -c gw -f
complete -c gw -n __fish_use_subcommand -a '(command gw --complete)'

complete -c gw -n '__fish_seen_subcommand_from branch; and not __fish_seen_subcommand_from status delete' \
    -a '(command gw branch --complete)'
complete -c gw -n '__fish_seen_subcommand_from branch; and __fish_seen_subcommand_from status delete' \
    -a '(command gwbs --complete)'
complete -c gw -n '__fish_seen_subcommand_from branch; and __fish_seen_subcommand_from status delete' \
    -l no-fetch -d 'use the refs already here'
complete -c gw -n '__fish_seen_subcommand_from branch; and __fish_seen_subcommand_from status delete' \
    -l force-refresh -d 'fetch even when the last fetch is under 10 minutes old'
complete -c gw -n '__fish_seen_subcommand_from branch; and __fish_seen_subcommand_from status delete' \
    -l no-forge -d 'never ask GitHub or GitLab'
complete -c gw -n '__fish_seen_subcommand_from branch; and __fish_seen_subcommand_from delete' \
    -l all -d 'assess all local branches for deletion'
complete -c gw -n '__fish_seen_subcommand_from branch; and __fish_seen_subcommand_from delete' \
    -l dry-run -d 'report without deleting'
complete -c gw -n '__fish_seen_subcommand_from branch; and __fish_seen_subcommand_from delete' \
    -s y -l yes -d 'skip deletion confirmation'

complete -c gw -n '__fish_seen_subcommand_from list l ls' \
    -a '(command gwl --complete)'
complete -c gw -n '__fish_seen_subcommand_from remove rm' \
    -a '(command gwr --complete)'

complete -c gw -n 'not __fish_use_subcommand' -l json -d 'the result as data'
complete -c gw -n 'not __fish_use_subcommand' -s q -l quiet -d 'less output'
complete -c gw -n 'not __fish_use_subcommand' -s v -l verbose -d 'print every git command'
complete -c gw -n 'not __fish_use_subcommand' -l explain -d 'print every git command and exit'

complete -c gw -s h -l help -d 'show this help message and exit'
