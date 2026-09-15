COMMON_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/agent-workflow"

module_path \
  "bin/project-settings" \
  "$HOME/.local/bin/agent-project-settings"

module_path \
  "bin/git-workflow" \
  "$HOME/.local/bin/git-workflow"

module_path \
  "instructions.md" \
  "$COMMON_CONFIG_DIR/instructions.md"
