---
name: status
description: Show docsync state for this repo: pages, audit tail, pending undocumented changes, drift vs Confluence.
disable-model-invocation: true
allowed-tools: Bash Read
---
> **Paths.** A repo holds one docsync project per team or workstream. `<project_dir>` is `.docsync/projects/<project>/` (or `.docsync/` in a single-project repo) and holds `config.yaml`, `project-context.md`, `audit.jsonl`. `<docs_dir>` is the `docs_dir` in that config (default `docs/<project>/`, or `docs/confluence/` in a single-project repo). `python3 .docsync/bin/docsync.py status` lists the projects. With several, work on the project whose `watch_paths` match the change and pass `--project <project>` to every docsync command.


1. `python3 .docsync/bin/docsync.py status`
2. `python3 .docsync/bin/docsync.py audit show --last 10`
3. If Confluence credentials are set: `python3 .docsync/bin/docsync.py plan` for drift (orphans, manual edits, pending updates).
4. Summarise in under ten lines: project, page count, undocumented watched changes (yes/no), drift, and the single next action.
