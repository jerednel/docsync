---
name: init
description: Set up docsync in the current project repo and produce the first full documentation pass.
disable-model-invocation: true
argument-hint: "--space <KEY> --root-page <url-or-id> [--title-prefix '[X] '] [--project-name <name>] [--publish-mode ci|local]"
allowed-tools: Bash Read Edit Write Grep Glob Skill
---

You are onboarding a new project. Output: a configured `.docsync/`, a filled-in project brief, nine populated
pages, a CI workflow, and (if credentials exist) a first publish.

1. **Inputs.** Parse `$ARGUMENTS` for `--space`, `--root-page`, `--title-prefix`, `--project-name`, `--publish-mode`,
   `--docs-dir`. If the repo already has a docsync project (`python3 .docsync/bin/docsync.py status`), this init ADDS a
   project: `--project-name` is required, the pages default to `docs/<project>/`, and the existing project is moved to
   `.docsync/projects/<project>/` automatically (commit those moves in the same PR). Shared repos should scope
   `watch_paths` to the project's own code so other teams' PRs are not gated.
   Ask the user for any that are missing. Publish mode: `ci` when they can add GitHub secrets (Actions publishes on
   merge); `local` when they cannot (they merge with `/docsync:merge`, which publishes from their machine). The root page is the ONLY Confluence page whose subtree will be written; confirm the URL.
2. **Bootstrap.** `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/docsync.py init <args>`. First project in a repo: writes
   `.docsync/config.yaml`, `.docsync/project-context.md`, `.docsync/audit.jsonl`, `.docsync/bin/` (vendored tool),
   `docs/confluence/*.md` templates and `.github/workflows/docsync.yml`. Additional project: writes
   `.docsync/projects/<project>/{config.yaml,project-context.md,audit.jsonl}`, `docs/<project>/*.md`, keeps shared settings
   (trunk, publish mode) in `.docsync/config.yaml`, and adds the docs dir to the workflow's publish `paths:`.
   Every later step below uses that project's files; pass `--project <project>` to docsync commands when there are several. Review `watch_paths`/`ignore_paths` in the config against the
   repo layout and adjust.
3. **Project brief.** Fill `.docsync/project-context.md` from everything available: README, CLAUDE.md, diagrams,
   dbt/airflow configs, any notes or data dictionaries the user pastes or points to. Ask the user the questions
   the template raises that the repo cannot answer (owners, cadences, SLAs, quality bars). Mark what remains
   unknown as `TODO:` so it is visible. Done when every section has content or an explicit TODO.
4. **Connectivity.** If `CONFLUENCE_*` env vars are set, run `python3 .docsync/bin/docsync.py doctor`. Otherwise
   tell the user which three variables to set (locally and as GitHub secrets) and continue.
5. **First documentation pass.** Invoke the `docsync:backfill` skill. It pulls any pages already under the root,
   inventories the codebase, and rewrites every page so it matches the code today.
6. **Preview and publish.** `python3 .docsync/bin/docsync.py plan --out`; show the summary and point the user to
   `.docsync/out/*.xml`. Ask whether to publish now; if yes, `push`. Otherwise CI publishes on the first merge.
7. **Hand over.** Tell the user which files to commit, then the loop for their mode:
   - ci: add the three `CONFLUENCE_*` secrets; build → `/docsync:update` → PR → merge → Confluence updates itself.
   - local: build → `/docsync:update` → PR → review → `/docsync:merge <pr>` merges and publishes from this machine.
