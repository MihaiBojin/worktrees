function gwr --wraps gwr --description 'cd to where gwr lands you'
    # The binary prints one destination and nothing else. `string collect`
    # because fish splits command substitution on newlines and a path may
    # hold one; $pipestatus[1] because the pipeline's own $status belongs to
    # `string collect`, which returns 1 whenever it collected nothing, which
    # is the failure case.
    set -l dest (command gwr $argv | string collect)
    set -l code $pipestatus[1]
    test $code -eq 0; or return $code
    test -n "$dest"; or return 0
    cd -- $dest
end
