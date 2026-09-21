---
name: check
description: Read-only documentation check for a branch or PR. Reports which changes need documentation and whether the branch already documents them. Used by CI and before review.
disable-model-invocation: true
argument-hint: "[--base <ref>]"
allowed-tools: Bash Read Grep Glob
---
> **Paths.** A repo holds one docsync case per project. `<case_dir>` is `.docsync/cases/<case>/` (or `.docsync/` in a single-case repo) and holds `config.yaml`, `case-context.md`, `audit.jsonl`. `<docs_dir>` is the `docs_dir` in that config (default `docs/<case>/`, or `docs/confluence/` in a single-case repo). `python3 .docsync/bin/docsync.py status` lists the cases. With several, work on the case whose `watch_paths` match the change and pass `--case <case>` to every docsync command.


Read-only. You classify and report; you change nothing.

1. Read `<case_dir>/case-context.md`. Run `python3 .docsync/bin/docsync.py changed $ARGUMENTS` and read the diff of
   every `watch`/`other` file (`git diff <merge_base>..HEAD -- <path>`).
2. Classify each logical change with `${CLAUDE_PLUGIN_ROOT}/reference/classification.md`.
3. Compare with what the branch already did: diff of `<docs_dir>/` and new lines in `<case_dir>/audit.jsonl`
   since the merge base. A documentable change is covered when a page edit addresses it AND an audit entry exists.
4. Print exactly this, then stop:

```
DOCSYNC CHECK: PASS|FAIL
| change | verdict | category | covered by |
...
Missing: <list of documentable changes with no page edit or audit entry, or "none">
```
Exit code matters for CI: if anything is missing, end with `exit 1` via Bash.
