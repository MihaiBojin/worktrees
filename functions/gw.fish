function gw --wraps gw --description 'cd to where gw list, add and remove land you'
    set -l lands 0
    switch "$argv[1]"
        case list l
            isatty stdin; and set lands 1
        case add a remove rm
            set lands 1
    end
    for arg in $argv
        test "$arg" != --; or break
        if contains -- "$arg" --json --list -l --help -h --complete --explain; set lands 0; end
    end
    if test $lands -eq 0
        command gw $argv
        return $status
    end
    # Keep every path byte; remove only the destination record delimiter.
    set -l output (command gw $argv | string collect -N)
    set -l code $pipestatus[1]
    set -l dest (string split -r -m1 \n -- "$output")[1]
    # A removal's destination stands even when later cleanup failed.
    if test $code -eq 0; or contains -- "$argv[1]" remove rm
        if string match -q -- '/*' "$dest"; and test -d "$dest"
            cd -- "$dest"; or return $status
            return $code
        end
    end
    printf '%s' "$output"
    return $code
end
