# ATP zsh tab completion for pytest --profile and --profiles-dir.
#
# Add one line to your ~/.zshrc to enable permanently:
#   source /home/didi/projects/atp/scripts/atp-completion.zsh

_atp_profile_names() {
    # Resolve the profiles directory: honour --profiles-dir if already typed.
    local profiles_dir="${PWD}/profiles"
    local i
    for ((i = 2; i < CURRENT; i++)); do
        if [[ "${words[$i]}" == "--profiles-dir" ]]; then
            profiles_dir="${words[$((i+1))]}"
            break
        elif [[ "${words[$i]}" == --profiles-dir=* ]]; then
            profiles_dir="${words[$i]#*=}"
            break
        fi
    done

    local profiles=()
    for f in "${profiles_dir}"/*.yaml(N); do
        local stem="${f:t:r}"   # basename without .yaml
        [[ "$stem" != "base" ]] && profiles+=("$stem")
    done
    compadd -a profiles
}

_pytest_atp() {
    _arguments -s \
        '--profile=[Platform profile name]:profile:_atp_profile_names' \
        '--profiles-dir=[Override profiles directory]:directory:_directories' \
        '--html=[Write HTML report to file]:output file:_files -g "*.html"' \
        '--pdf=[Write PDF report to file]:output file:_files -g "*.pdf"' \
        '*:: :->passthrough'

    # Let zsh's default completion handle every other argument (pytest flags, paths…)
    [[ $state == passthrough ]] && _default
}

compdef _pytest_atp pytest
