---
name: push
description: Publish <docs_dir> pages to the scoped Confluence tree now (normally CI does this on merge).
disable-model-invocation: true
argument-hint: "[--force] [--respect-manual-edits] [--pr <n>]"
allowed-tools: Bash Read
---
> **Paths.** A repo holds one docsync case per project. `<case_dir>` is `.docsync/cases/<case>/` (or `.docsync/` in a single-case repo) and holds `config.yaml`, `case-context.md`, `audit.jsonl`. `<docs_dir>` is the `docs_dir` in that config (default `docs/<case>/`, or `docs/confluence/` in a single-case repo). `python3 .docsync/bin/docsync.py status` lists the cases. With several, work on the case whose `watch_paths` match the change and pass `--case <case>` to every docsync command.


1. Confirm `CONFLUENCE_BASE_URL`, `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN` are set (`env | grep CONFLUENCE_ | sed 's/=.*/=set/'`).
   If not, tell the user how to set them and stop.
2. `python3 .docsync/bin/docsync.py doctor` — root page, space match, in-scope tree. Stop on MISMATCH.
3. `python3 .docsync/bin/docsync.py plan --out` and show the created/updated/unchanged/orphan/manual-edit summary.
4. If the current branch is not the trunk, say so: publishing from a feature branch puts unmerged content on
   Confluence. Proceed only if the user asked for that explicitly.
5. `python3 .docsync/bin/docsync.py push --json $ARGUMENTS`. Report the summary and any warnings verbatim.
