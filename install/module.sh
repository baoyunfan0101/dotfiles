WORKFLOW_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/agent-workflow"

module_path \
  "workflow/bin/agent-project" \
  "$HOME/.local/bin/agent-project"

module_path \
  "workflow/bin/git-workflow" \
  "$HOME/.local/bin/git-workflow"

module_path \
  "workflow/libexec/agent-workflow-opt-in" \
  "$HOME/.local/libexec/agent-workflow-opt-in"

module_path \
  "workflow/libexec/git-workflow" \
  "$HOME/.local/libexec/git-workflow"

module_path \
  "workflow/lib/git-workflow" \
  "$HOME/.local/lib/git-workflow"

module_path \
  "workflow/lib/agent_project" \
  "$HOME/.local/lib/agent_project"

module_path \
  "workflow/instructions.md" \
  "$WORKFLOW_CONFIG_DIR/instructions.md"

module_path \
  "instructions.md" \
  "${XDG_CONFIG_HOME:-$HOME/.config}/dotfiles/instructions.md"

module_path \
  "rules" \
  "${XDG_CONFIG_HOME:-$HOME/.config}/dotfiles/rules"
