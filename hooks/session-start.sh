#!/usr/bin/env bash
# Adds a short docsync reminder to the session context when the cwd is a repo with docsync configured.
# Handles both layouts: single case (.docsync/config.yaml is the case) and multi case (.docsync/cases/<case>/).
root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
[ -d "$root/.docsync" ] || exit 0
docs_of() { grep -E '^docs_dir:' "$1" | sed -E 's/^docs_dir: *"?([^" #]*)"?.*/\1/'; }
if ls "$root"/.docsync/cases/*/config.yaml >/dev/null 2>&1; then
  names=()
  for c in "$root"/.docsync/cases/*/config.yaml; do
    name=$(basename "$(dirname "$c")"); names+=("$name")
    echo "docsync case '$name': config .docsync/cases/$name/config.yaml, brief .docsync/cases/$name/case-context.md, pages $(docs_of "$c")/."
  done
  [ ${#names[@]} -gt 1 ] && echo "Several cases share this repo: pass --case <name> to docsync.py commands and edit only the case whose watch_paths match your change."
else
  cfg="$root/.docsync/config.yaml"
  [ -f "$cfg" ] || exit 0
  case=$(grep -E '^case_name:' "$cfg" | sed -E 's/^case_name: *"?([^"]*)"?/\1/')
  echo "docsync is active for case '${case:-unknown}' (config: .docsync/config.yaml, brief: .docsync/case-context.md, pages: $(docs_of "$cfg")/)."
fi
echo "Documentation is docs-as-code. Before a PR is opened, run /docsync:update so pipeline changes are evaluated and either documented in the case's pages or recorded as excluded in its audit.jsonl. CI blocks PRs that change watched code without either. Another project can join this repo with /docsync:init --case-name <name>."
