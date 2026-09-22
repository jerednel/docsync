# Using docsync on a project

## 0. One-time on your machine
1. `claude plugin marketplace add jerednel/docsync && claude plugin install docsync@jeremy-tools`
2. `pip install -r ~/git/docsync/scripts/requirements.txt`
3. Create an Atlassian API token: https://id.atlassian.com/manage-profile/security/api-tokens
4. Put these in your shell profile (or a direnv `.envrc` per project):
   ```bash
   export CONFLUENCE_BASE_URL="https://<site>.atlassian.net/wiki"
   export CONFLUENCE_EMAIL="you@example.com"
   export CONFLUENCE_API_TOKEN="..."
   ```

## 1. New project
1. In Confluence, create (or pick) the **one page** that will be the root of the data documentation. Copy its URL.
2. In the project repo, on a branch: `claude` then
   `/docsync:init --space <KEY> --root-page <url> --title-prefix "[<Project>] " --project-name "<Project>" --publish-mode local|ci`
   Pick `local` if you cannot add GitHub secrets: you will merge with `/docsync:merge`, which publishes from your
   machine. Pick `ci` if you can: Actions publishes on merge.
3. Answer its questions about owners, cadences, SLAs and quality bars. Paste any notes, data dictionaries or
   diagrams you have; they go into `.docsync/project-context.md`, which is the brief the agent reads every time.
4. Init ends by running `/docsync:backfill`: it pulls any pages already under the root into `.docsync/imported/`
   (inputs only, git-ignored), inventories the codebase, and rewrites every page to match the code today. Review
   the generated pages (`docs/confluence/*.md`), the dry-run XML in `.docsync/out/`, and the orphan list it prints.
5. Commit and open the PR. In ci mode add repo secrets `CONFLUENCE_BASE_URL`, `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN`.
6. Merge: ci mode → the `publish` job runs; local mode → `/docsync:merge <pr>`. Check the Change Log page appeared.

### Existing project with documentation already on Confluence
Same steps. Point `--root-page` at the existing section's parent page. Backfill harvests owners, cadences and
decisions from the old pages into the new structure and `project-context.md`. Old pages whose titles match the new
ones are overwritten on publish; the rest are reported as orphans for you to archive by hand. Run
`/docsync:backfill` again any time you suspect drift; it is idempotent.

Files init adds to the repo:
```
.docsync/config.yaml          scope, watch/ignore globs, prefix       (commit)
.docsync/project-context.md      the brief                                (commit, keep current)
.docsync/audit.jsonl          audit log                                (commit, append-only)
.docsync/bin/docsync.py       vendored tool for CI and worktrees       (commit)
docs/confluence/*.md          the pages                                (commit)
.github/workflows/docsync.yml gate + publish                           (commit)
```

## 2. Daily loop (worktrees included)
```
git worktree add ../<repo>.worktrees/<story> -b <story>
cd ../<repo>.worktrees/<story>
claude              # SessionStart hook reminds you docsync is active
  ... build the feature ...
> /docsync:update   # or: /docsync:update "moved landing path to s3://.../v2 and added returns netting"
```
`update` reads the diff against `origin/main`, classifies every logical change, edits the pages, appends audit
entries, runs the gate, and lists the files to commit. Commit them with your code. Open the PR.

The gate job fails if you changed pipeline code and touched neither `docs/confluence/` nor `.docsync/audit.jsonl`.
Fix: run `/docsync:update`. If the PR genuinely has no documentation impact, the agent records `excluded`
entries, which also satisfies the gate. For pure tooling PRs, add the `docs-not-needed` label.

On merge, only the pages whose content changed are pushed and the Change Log is regenerated:
- **ci mode**: the `publish` job does it.
- **local mode**: `/docsync:merge <pr-number>` does it. It refuses to merge a PR whose pipeline changes have no
  docs or audit change (unless labelled `docs-not-needed`), refuses on failing checks or conflicts, merges with
  `gh pr merge --squash` (or `--method merge|rebase`, `--delete-branch`), fetches trunk, publishes from the trunk
  worktree (or a temporary detached one, so your feature worktree is untouched), and cleans up. If publish fails
  after the merge, rerun `python3 .docsync/bin/docsync.py push --pr <n>` from a trunk checkout.

## 3. When the brief changes, not the code
Owner changed, a provider moved their delivery to Tuesdays, a new SLA: edit `.docsync/project-context.md`
and the relevant page (usually `10-who-does-what.md`), run `/docsync:update "process change: ..."` so an
`ownership-process` audit entry is recorded, and merge.

## 4. Manual publish, drift, orphans
- `/docsync:status` — pages, audit tail, undocumented watched changes, drift.
- `/docsync:push` — publish now (doctor → plan → push). Warn if not on trunk.
- **Manual edit detected**: someone edited a page in Confluence. Repo wins by default. To keep their edit,
  copy it into the markdown file and merge that.
- **Orphan**: a Confluence page under the root with no source file. docsync never deletes; archive it by hand.
- Re-publish everything: Actions → docsync → Run workflow → force = true.

## 5. Adding or removing pages
See `reference/page-model.md`. New page = new `docs/confluence/NN-slug.md` with `title:` frontmatter and a row
in Start Here. Removing = delete the file, archive the orphan by hand.

## 6. Multiple repos on one project
Point each repo's `.docsync/config.yaml` at a **different** root page (or the same root with different
`title_prefix` values, e.g. `[DATA ingest] `, `[DATA dbt] `). Orphan detection works per root, so with a shared
root every repo will report the other repo's pages as orphans; separate roots are cleaner.

## 7. Updating the tool in a project repo
After changing the plugin (and `claude plugin marketplace update jeremy-tools && claude plugin update docsync@jeremy-tools`): `python3 ~/git/docsync/scripts/docsync.py upgrade` inside the project repo,
commit `.docsync/bin/`. `upgrade` also renames the pre-0.3 `.docsync/cases/` layout, `case-context.md` and `case_name:` to their
`project` equivalents; commit those moves too.

## Command reference (`python3 .docsync/bin/docsync.py …`)
| Command | Does |
|---|---|
| `doctor` | credentials, root page, space check, in-scope tree |
| `changed [--base ref]` | JSON of changed files bucketed watch / ignore / docs / other |
| `gate [--base ref]` | exit 1 if watched code changed without docs/audit changes |
| `render <file>` | storage XML for one page |
| `plan [--out]` | dry run; XML to `.docsync/out/` |
| `push [--force] [--respect-manual-edits] [--pr n] [--commit sha] [--json]` | publish |
| `audit add --json '…' / show / validate / render` | audit log |
| `status` | offline summary |
| `pull` | download pages under the root to `.docsync/imported/` (backfill input) |
| `merge-publish --pr n [--method squash] [--delete-branch] [--force] [--dry-run]` | local mode: verify, merge with gh, publish from trunk |
| `upgrade` | refresh vendored tool |

## Several projects in one repo

See "Several projects in one repo" in the README: `.docsync/projects/<project>/` per project, shared settings in
`.docsync/config.yaml`, `docs/<project>/` for the pages, `--project <name>` on commands when more than one project exists.
Scratch output moves to `.docsync/out/<project>/` and `.docsync/imported/<project>/`.
