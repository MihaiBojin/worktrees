# gw [QUERY], or gw COMMAND: run COMMAND, or pick a worktree and cd into it.
function gw --wraps gw --description 'run gw, and cd to where gw list lands you'
    # Every subcommand but list goes straight to the binary: capturing it
    # would hold gwp's table back until after its prompt and strip its
    # colour. The names come from the CLI, never from this file.
    set -l subs (command gw --complete | string replace -r '\t.*' '')
    if test "$argv[1]" != list; and contains -- "$argv[1]" $subs
        command gw $argv
        return
    end
    # The rest is gwa's shim: `string collect` keeps a path holding a
    # newline whole, and $pipestatus[1] is the binary's code rather than
    # `string collect`'s. Anything that is not a directory is what --json or
    # --list asked for, so it is printed.
    set -l dest (command gw $argv | string collect)
    set -l code $pipestatus[1]
    test $code -eq 0; or return $code
    test -n "$dest"; or return 0
    test -d "$dest"; or begin; printf '%s\n' $dest; return 0; end
    cd -- $dest
end
