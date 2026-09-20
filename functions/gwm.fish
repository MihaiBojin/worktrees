function gwm --wraps gwm --description 'cd to where gwm lands you'
    # The binary prints one destination, or the data `--json` and `--list`
    # were asked for, on the same stream. `string collect`
    # because fish splits command substitution on newlines and a path may
    # hold one; $pipestatus[1] because the pipeline's own $status belongs to
    # `string collect`, which returns 1 whenever it collected nothing, which
    # is the failure case.
    set -l dest (command gwm $argv | string collect)
    set -l code $pipestatus[1]
    test $code -eq 0; or return $code
    test -n "$dest"; or return 0
    # A directory or nothing. --json and --list print what was asked for on
    # the same stream a destination arrives on, and the shim cannot tell one
    # from the other by looking; it can ask whether it is somewhere to go.
    test -d "$dest"; or return 0
    cd -- $dest
end
