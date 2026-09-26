CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

module_managed_block \
  "AGENTS.md" \
  "$CODEX_HOME/AGENTS.md" \
  "<!-- BEGIN baoyunfan0101/dotfiles managed block -->" \
  "<!-- END baoyunfan0101/dotfiles managed block -->"

module_dir_contents \
  "skills" \
  "$CODEX_HOME/skills"
