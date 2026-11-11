WORKFLOW_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/agent-workflow"

module_path \
  "tools/bin/agent-project" \
  "$HOME/.local/bin/agent-project"

module_path \
  "tools/bin/git-workflow" \
  "$HOME/.local/bin/git-workflow"

module_path \
  "tools/libexec/agent-workflow-opt-in" \
  "$HOME/.local/libexec/agent-workflow-opt-in"

module_path \
  "tools/libexec/git-workflow" \
  "$HOME/.local/libexec/git-workflow"

module_path \
  "tools/lib/git-workflow" \
  "$HOME/.local/lib/git-workflow"

module_path \
  "tools/lib/agent_project" \
  "$HOME/.local/lib/agent_project"

# Preserve the installed rule path for older shared instructions.
module_path \
  "rules/git-workflow.md" \
  "$WORKFLOW_CONFIG_DIR/instructions.md"

module_path \
  "instructions.md" \
  "${XDG_CONFIG_HOME:-$HOME/.config}/dotfiles/instructions.md"

module_path \
  "rules" \
  "${XDG_CONFIG_HOME:-$HOME/.config}/dotfiles/rules"
