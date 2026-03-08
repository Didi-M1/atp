#!/usr/bin/env zsh
# Load ATP tab completion for this shell session.
#
# Run once per session:
#   source scripts/enable-completion.sh
#
# Or add to ~/.zshrc for permanent effect:
#   source /home/didi/projects/atp/scripts/atp-completion.zsh

source "$(dirname "$0")/atp-completion.zsh"
echo "ATP completion enabled. Try: pytest --profile <TAB>"
