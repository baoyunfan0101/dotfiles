COMMON_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/agent-workflow"

module_path \
  "bin/agent-project" \
  "$HOME/.local/bin/agent-project"

module_path \
  "bin/git-workflow" \
  "$HOME/.local/bin/git-workflow"

module_path \
  "libexec/git-workflow" \
  "$HOME/.local/libexec/git-workflow"

module_path \
  "lib/git-workflow" \
  "$HOME/.local/lib/git-workflow"

module_path \
  "instructions.md" \
  "$COMMON_CONFIG_DIR/instructions.md"
