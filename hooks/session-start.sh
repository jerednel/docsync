#!/usr/bin/env bash
# Adds a short docsync reminder to the session context when the cwd is a repo with docsync configured.
# Handles both layouts: single project (.docsync/config.yaml is the project) and multi project (.docsync/projects/<project>/).
root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
[ -d "$root/.docsync" ] || exit 0
[ -d "$root/.docsync/cases" ] && [ ! -d "$root/.docsync/projects" ] && echo "docsync: this repo uses the pre-0.3 .docsync/cases/ layout. Run python3 .docsync/bin/docsync.py upgrade (after updating the plugin) to rename it to .docsync/projects/ and commit."
docs_of() { grep -E '^docs_dir:' "$1" | sed -E 's/^docs_dir: *"?([^" #]*)"?.*/\1/'; }
if ls "$root"/.docsync/projects/*/config.yaml >/dev/null 2>&1; then
  names=()
  for c in "$root"/.docsync/projects/*/config.yaml; do
    name=$(basename "$(dirname "$c")"); names+=("$name")
    echo "docsync project '$name': config .docsync/projects/$name/config.yaml, brief .docsync/projects/$name/project-context.md, pages $(docs_of "$c")/."
  done
  [ ${#names[@]} -gt 1 ] && echo "Several projects share this repo: pass --project <name> to docsync.py commands and edit only the project whose watch_paths match your change."
else
  cfg="$root/.docsync/config.yaml"
  [ -f "$cfg" ] || exit 0
  project=$(grep -E '^project_name:' "$cfg" | sed -E 's/^project_name: *"?([^"]*)"?/\1/')
  echo "docsync is active for project '${project:-unknown}' (config: .docsync/config.yaml, brief: .docsync/project-context.md, pages: $(docs_of "$cfg")/)."
fi
echo "Documentation is docs-as-code. Before a PR is opened, run /docsync:update so pipeline changes are evaluated and either documented in the project's pages or recorded as excluded in its audit.jsonl. CI blocks PRs that change watched code without either. Another project can join this repo with /docsync:init --project-name <name>."
