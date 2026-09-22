---
name: backfill
description: First-time reconciliation for an existing project. Pulls whatever already exists under the Confluence root, inventories the whole codebase, rewrites every page so it matches the code today, and records the gaps closed. Run once after init, or whenever docs are suspected to have drifted; incremental /docsync:update takes over afterwards.
disable-model-invocation: true
argument-hint: "[--no-pull] [notes]"
allowed-tools: Bash Read Edit Write Grep Glob
---
> **Paths.** A repo holds one docsync project per team or workstream. `<project_dir>` is `.docsync/projects/<project>/` (or `.docsync/` in a single-project repo) and holds `config.yaml`, `project-context.md`, `audit.jsonl`. `<docs_dir>` is the `docs_dir` in that config (default `docs/<project>/`, or `docs/confluence/` in a single-project repo). `python3 .docsync/bin/docsync.py status` lists the projects. With several, work on the project whose `watch_paths` match the change and pass `--project <project>` to every docsync command.


Goal: after this run, every pipeline, table, storage location, rule, owner and convention in the repo is on
exactly one page, every statement on every page is true of the code at HEAD, and nothing that only existed
in old Confluence pages has been lost. Then `/docsync:update` only ever has to handle deltas.

Read before step 3: `${CLAUDE_PLUGIN_ROOT}/reference/page-model.md`, `classification.md`, `writing-style.md`.

## Steps

1. **Harvest what exists.** Unless `--no-pull` or credentials are missing:
   `python3 .docsync/bin/docsync.py pull` saves every page under the root to `.docsync/imported/[<project>/]*.txt` with an
   `index.json`. Read them all. Also read `<project_dir>/project-context.md`, `README*`, `CLAUDE.md`, `docs/**`, diagram
   files, data dictionaries, and any notes in `$ARGUMENTS`. Done when you have a list of every fact these sources
   assert (owners, cadences, SLAs, paths, tables, rules), each tagged with its source.

2. **Inventory the code.** Enumerate, with file paths: every pipeline/DAG/job, every model/table/view (dbt models,
   DDL, `CREATE TABLE`, `to_sql`, Snowflake stages), every storage location (`s3://`, `gs://`, `abfss://`, bucket
   names in config/terraform), every schedule (cron, `schedule_interval`, workflow triggers), every DQ test or
   assertion, and every logging/observability convention. Use Grep across the repo, then read each hit's context.
   Done when the inventory has no entry you have not opened.

3. **Reconcile.** Build a three-way comparison per entity: in code / in current `<docs_dir>` pages / in
   imported Confluence pages. Classify each row:
   - **missing**: in code, not documented → write it on its home page.
   - **stale**: documented, but the code says otherwise → rewrite the statement.
   - **dead**: documented, absent from code → remove it; if it is an owner/process fact not derivable from code,
     keep it and mark "Unknown — <role> to confirm" if it cannot be verified.
   - **harvest**: only in imported pages (owners, cadences, decisions) and still plausible → move it to its home
     page and to `<project_dir>/project-context.md`.
   Done when every row has one of these four outcomes.

4. **Rewrite the pages.** Apply the outcomes page by page, following the page model (one home per fact) and
   writing rules. Start Here gets the map and the who-to-ask table. Assumptions become numbered rules with
   "where implemented" file paths. Replace every template placeholder. Update `<project_dir>/project-context.md` with
   harvested facts so future runs have the brief. Done when `grep -rn "TODO\|TBC\|PIPELINE_NAME\|SCHEMA.TABLE_NAME\|HAND-OFF NAME" <docs_dir>` returns nothing.

5. **Record.** One audit entry with `category: backfill`, `verdict: documented`, `files: ["<repo>"]`, `pages:`
   every page rewritten, and a `rationale` that counts missing/stale/dead/harvested items closed. Add separate
   entries for any business rule you discovered in code that no document had ever stated (`business-logic`),
   since those are material findings for the project team.

6. **Verify and preview.** `status` (no warnings), `render` each page, `gate` if on a branch, and
   `plan --out` if credentials exist. Existing Confluence pages whose titles match will be **overwritten** on
   publish; pages that do not match are reported as orphans and left alone. Show the user the plan summary and
   the orphan list, and recommend which orphans to archive by hand.

7. **Report.** A table of entities by outcome (missing / stale / dead / harvested) with counts, the list of
   files to commit, and a short list of facts still marked Unknown with the role who must confirm each.
   Publishing happens on merge (CI mode) or via `/docsync:merge` (local mode).
