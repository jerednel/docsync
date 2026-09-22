---
name: update
description: Evaluate the code changes in this branch or worktree for documentation impact and update the docs-as-code Confluence pages plus audit log. Use when the user finished a feature in a repo that has .docsync/, says "document this change", "update the docs", "sync confluence", or is about to open a PR. For first-time or full re-documentation use /docsync:backfill instead.
argument-hint: "[--base <ref>] [notes about the change]"
allowed-tools: Bash Read Edit Write Grep Glob
---
> **Paths.** A repo holds one docsync project per team or workstream. `<project_dir>` is `.docsync/projects/<project>/` (or `.docsync/` in a single-project repo) and holds `config.yaml`, `project-context.md`, `audit.jsonl`. `<docs_dir>` is the `docs_dir` in that config (default `docs/<project>/`, or `docs/confluence/` in a single-project repo). `python3 .docsync/bin/docsync.py status` lists the projects. With several, work on the project whose `watch_paths` match the change and pass `--project <project>` to every docsync command.


You are keeping Confluence documentation in lock-step with the code. The docs are markdown files in
`<docs_dir>/` (one file = one page). You edit those files; CI publishes them on merge. Nothing is stale
because every documentable change ships in the same PR as the code.

Arguments: `$ARGUMENTS`. `--base <ref>` overrides the trunk to diff against. If the user asks for a full pass or
the docs are still template placeholders, stop and run the `docsync:backfill` skill instead. Any other text is the developer's notes about the change:
treat it as intent, verify it against the diff.

Tool: `python3 .docsync/bin/docsync.py` (vendored in the repo; works in every worktree). If `.docsync/` is
missing, stop and tell the user to run `/docsync:init` first.

Rubric and rules — read all three before step 3, they are short:
- `${CLAUDE_PLUGIN_ROOT}/reference/classification.md` (what counts, audit entry shape)
- `${CLAUDE_PLUGIN_ROOT}/reference/page-model.md` (which page a fact lives on)
- `${CLAUDE_PLUGIN_ROOT}/reference/writing-style.md` (how to write it)

## Steps

1. **Load the brief.** Read `<project_dir>/project-context.md` and `<project_dir>/config.yaml`. Skim `<docs_dir>/*.md`
   so you know what is already documented. Done when you can say in one sentence what this project's data is for
   and who the readers are.

2. **Scope the change.** Run `python3 .docsync/bin/docsync.py changed [--base <ref>]` and read the JSON.
   For every file in bucket `watch` or `other`, read the actual diff:
   `git diff <merge_base>..HEAD -- <path>` and `git diff -- <path>` for uncommitted work. Read enough surrounding
   code to understand behaviour, not just the hunk.
   Done when you can list every logical change.

3. **Classify.** Group hunks into logical changes. Apply the rubric to each. For every change decide
   `documented` + category + home page(s), or `excluded` + reason. Done when every logical change has a verdict
   and no verdict rests on a guess you could have checked in the code.

4. **Edit the pages.** For each documented change: open the home page, find every existing statement the change
   makes false, rewrite it. Add new rows/sections using the page's existing headings. Then
   `grep -ril "<table|path|term>" <docs_dir>/` and fix every other mention so pages agree. Fill in exact
   strings from the code. Done when no touched page contains a placeholder, a TODO, or a statement contradicted
   by the current code.

5. **Record the audit.** One `audit add` per logical change, documented and excluded alike:
   `python3 .docsync/bin/docsync.py audit add --json '<entry>'`. Done when the number of entries added equals
   the number of logical changes from step 3.

6. **Verify.** Run, and fix anything that fails:
   - `python3 .docsync/bin/docsync.py status` (no warnings)
   - `python3 .docsync/bin/docsync.py render <docs_dir>/<each edited file>` (prints XML, no error)
   - `python3 .docsync/bin/docsync.py gate [--base <ref>]` (PASS)
   - If Confluence credentials are in the environment: `python3 .docsync/bin/docsync.py plan` to preview.

7. **Report.** Show the user a table: change → verdict → category → page(s). Then the exact files to commit
   (`<docs_dir>/...`, `<project_dir>/audit.jsonl`) and remind them these go in the same PR as the code.
   Do not commit unless asked.

## Hard rules
- Never edit files outside `<docs_dir>/` and `<project_dir>/audit.jsonl` in this skill.
- Never delete a page file without saying so; orphaned Confluence pages are archived by a human.
- If the project context is silent on something the docs need (an owner, a cadence), write
  "Unknown — <role> to confirm" in the page and say so in the report. Do not invent facts.
