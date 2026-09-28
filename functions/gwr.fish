function gwr --wraps gwr --description 'cd to where gwr lands you'
    # The binary prints one destination, or the data `--json` and `--list`
    # were asked for, on the same stream. `string collect`
    # because fish splits command substitution on newlines and a path may
    # hold one; $pipestatus[1] because the pipeline's own $status belongs to
    # `string collect`, which returns 1 whenever it collected nothing, which
    # is the failure case.
    set -l dest (command gwr $argv | string collect)
    set -l code $pipestatus[1]
    test $code -eq 0; or return $code
    test -n "$dest"; or return 0
    # A directory is somewhere to go, and anything else is what --json or
    # --list was asked for, arriving on the same stream. The shim cannot tell
    # them apart by looking, so it asks, and prints what is not a directory.
    test -d "$dest"; or begin; printf '%s\n' $dest; return 0; end
    cd -- $dest
end
