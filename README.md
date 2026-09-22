# docsync — documentation agent for data-engineering projects

A Claude Code plugin that keeps one Confluence section in sync with the code on every data project.
Install it once; run `/docsync:init` in each new project repo.

## What it does

```
 developer worktree                    PR                       trunk (main)              Confluence
 ───────────────────                   ──                       ────────────              ──────────
 build feature ──► /docsync:update ──► gate: code changed ──►  merge ──► CI: docsync push ──► pages under
   (classify diff,  edit pages,          ⇒ docs or audit                (Confluence API,       the ONE root
    write audit entry)                   entry must change              scoped, hash-diffed)    page you chose
```

- **Docs-as-code.** Each Confluence page is a markdown file in the project's `docs_dir` (default `docs/confluence/`). Doc changes ride in the same
  PR as the code, so they merge with it and work naturally with git worktrees.
- **Every change is evaluated.** `/docsync:update` groups the diff into logical changes and classifies each one
  with a fixed rubric (`reference/classification.md`): business logic, storage paths, tables, new features,
  ownership/cadence, data quality, conventions are documented; refactors and immaterial infra are excluded but
  still recorded.
- **Audit log.** `.docsync/audit.jsonl` holds one entry per evaluated change. It is rendered to a generated
  "Change Log" page under the root.
- **Enforced, not hoped for.** The PR gate (`docsync gate`) fails when pipeline code changes without a docs or
  audit change. The `docs-not-needed` label bypasses it deliberately.
- **Published on merge** through the Confluence Cloud REST v2 API, in one of two modes. **ci**: GitHub Actions
  publishes using repo secrets. **local**: `/docsync:merge <pr>` verifies the PR is documented, merges it with `gh`,
  and publishes from your machine with your own credentials, so no repo secrets are needed. Pages are
  hash-compared, so only changed pages get a new version. Each page carries a banner with sync date, commit and PR.
- **Backfill for existing projects.** `/docsync:backfill` pulls whatever already sits under the Confluence root,
  inventories the whole codebase, reconciles every page against the code (missing / stale / dead / harvested),
  and records the gaps closed. Incremental `/docsync:update` takes over from there.
- **Scoped.** Every write is checked against the configured root page's subtree, plus a space-key check.
  Nothing is ever deleted; pages with no source file are reported as orphans.

## Layout

| Path | Purpose |
|---|---|
| `skills/init`, `backfill`, `update`, `check`, `merge`, `push`, `status` | The seven `/docsync:*` skills |
| `agents/data-doc-agent.md` | Subagent persona that runs `update`/`check` off the main thread |
| `reference/` | Classification rubric, page model, writing rules the skills read |
| `scripts/docsync.py` | The tool: init, doctor, changed, gate, render, plan, push, audit, status |
| `templates/` | Per-project config, project brief, nine page skeletons, GitHub workflow |
| `hooks/` | SessionStart reminder when the cwd is a docsync-enabled repo |
| `tests/` | Unit + end-to-end tests with an in-memory fake Confluence |
| `docs/USAGE.md`, `docs/TESTING.md` | Runbooks |

## Install once, use on every project

```bash
# from any directory
claude plugin marketplace add jerednel/docsync     # registers the marketplace "jeremy-tools" from GitHub
claude plugin install docsync@jeremy-tools         # installs for your user, all projects
claude plugin list                                 # confirm
```
Developing the plugin itself? Add the checkout instead: `claude plugin marketplace add ~/git/docsync`.

To make a project repo prompt teammates to install it, add to the repo's `.claude/settings.json`:
```json
{
  "extraKnownMarketplaces": { "jeremy-tools": { "source": { "source": "github", "repo": "jerednel/docsync" } } },
  "enabledPlugins": { "docsync@jeremy-tools": true }
}
```
The install is a **copy** (under `~/.claude/plugins/cache/jeremy-tools/docsync/<version>`). After editing the
plugin source, bump `version` in `.claude-plugin/plugin.json`, then:
```bash
claude plugin marketplace update jeremy-tools && claude plugin update docsync@jeremy-tools
```
Restart Claude Code to pick it up. To try uncommitted edits in one session without reinstalling: `claude --plugin-dir ~/git/docsync`.

Python side (once per machine and once per CI runner): `pip install -r scripts/requirements.txt`
(`requests`, `markdown`, `pyyaml`; Python 3.9+).

## Per project, in five lines

```bash
cd ~/git/<project-repo>
export CONFLUENCE_BASE_URL=https://<site>.atlassian.net/wiki CONFLUENCE_EMAIL=you@example.com CONFLUENCE_API_TOKEN=...
claude
> /docsync:init --space DATA --root-page https://<site>.atlassian.net/wiki/spaces/DATA/pages/123456/Data --title-prefix "[DATA] " --publish-mode local
```
`--publish-mode ci` instead if you can add the three `CONFLUENCE_*` values as GitHub Actions secrets. See `docs/USAGE.md`.

## Design choices worth knowing

- **Why markdown in the repo instead of writing Confluence directly from the agent?** Review in the PR, history
  in git, worktree-safe, deterministic publish step that needs no LLM in CI, and no drift between "what the code
  does" and "what we said" because both merge together.
- **Manual edits in Confluence** are detected (page version differs from the one docsync wrote) and overwritten
  by default with a loud warning; set `overwrite_manual_edits: false` to skip them instead.
- **Page order** under the root is not managed (v2 API has no position field); titles are prefixed with numbers
  in the filenames only. Sort in Confluence once by hand if you care.
- **Images** are not uploaded. Use mermaid code blocks or tables; the converter leaves a visible pointer for `<img>`.
- **Confluence titles are unique per space**, hence `title_prefix`.

## Limitations

- Confluence Cloud only (REST v2). Data Center would need the v1 endpoints in `Confluence`.
- Markdown subset: headings, lists, tables, fenced code, `> **Note:**`/`Warning`/`Tip`/`Info` panels, `[TOC]`,
  links to sibling `.md` files. Anything else passes through as HTML and is validated for well-formedness.
- The optional AI check in CI is commented out in the workflow template; it needs an `ANTHROPIC_API_KEY`
  secret and the plugin pushed to a git host.

## Several projects in one repo

A repo can hold one docsync **project** per team or workstream, each with its own Confluence root, title prefix,
docs dir, watch paths, brief and audit log:

```
.docsync/config.yaml                            shared: base_branch, publish_mode, overwrite_manual_edits, changelog_page
.docsync/projects/<project>/config.yaml         the project: confluence root, title_prefix, docs_dir, watch_paths
.docsync/projects/<project>/project-context.md
.docsync/projects/<project>/audit.jsonl
docs/<project>/*.md                             the pages
```

Add a project with `/docsync:init --project-name <name> --space <KEY> --root-page <url> --title-prefix '[<name>] '`.
The first `init` in a repo still creates the single-project layout; a second one moves that project into
`.docsync/projects/` (`docsync migrate` does the same by hand). `gate`, `push`, `status` and `doctor` run over every
project by default; `audit add` and any command aimed at one project take `--project <name>` (or `DOCSYNC_PROJECT`).
A change is gated only against the project whose `watch_paths` it touches.

Repos set up with docsync 0.2 used the word **case** for this (`.docsync/cases/`, `case-context.md`, `case_name`).
After updating the plugin, run `python3 .docsync/bin/docsync.py upgrade` in each such repo: it renames them in place and
keeps reading `case_name` until you do.
