function gwr --wraps gwr --description 'cd to where gwr lands you'
    # Keep every path byte; remove only the destination record delimiter.
    set -l output (command gwr $argv | string collect -N)
    set -l code $pipestatus[1]
    set -l dest (string split -r -m1 \n -- "$output")[1]
    # A destination means removal succeeded, even if later cleanup failed.
    if string match -q -- '/*' "$dest"; and test -d "$dest"
        cd -- "$dest"; or return $status
    else
        printf '%s' "$output"
    end
    return $code
end
