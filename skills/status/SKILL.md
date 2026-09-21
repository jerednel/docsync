---
name: status
description: Show docsync state for this repo: pages, audit tail, pending undocumented changes, drift vs Confluence.
disable-model-invocation: true
allowed-tools: Bash Read
---
> **Paths.** A repo holds one docsync case per project. `<case_dir>` is `.docsync/cases/<case>/` (or `.docsync/` in a single-case repo) and holds `config.yaml`, `case-context.md`, `audit.jsonl`. `<docs_dir>` is the `docs_dir` in that config (default `docs/<case>/`, or `docs/confluence/` in a single-case repo). `python3 .docsync/bin/docsync.py status` lists the cases. With several, work on the case whose `watch_paths` match the change and pass `--case <case>` to every docsync command.


1. `python3 .docsync/bin/docsync.py status`
2. `python3 .docsync/bin/docsync.py audit show --last 10`
3. If Confluence credentials are set: `python3 .docsync/bin/docsync.py plan` for drift (orphans, manual edits, pending updates).
4. Summarise in under ten lines: case, page count, undocumented watched changes (yes/no), drift, and the single next action.
