# Testing docsync

Three layers: unit/e2e tests without network, a dry run against your real Confluence, and a first live publish
into a sandbox root page.

## 1. Unit and end-to-end tests (no network, ~3 s)
```bash
cd ~/git/docsync
tests/run_tests.sh
```
What they cover:
- markdown → Confluence storage: code macros with language and CDATA, panels, page links with prefixed titles,
  `[TOC]`, comment stripping, tables, well-formedness of every template.
- scope guard: update outside root refused, root page never overwritten, create under a foreign parent refused.
- full cycle in a throwaway git repo against `tests/fake_confluence.py`: init → first sync creates 10 pages →
  second sync is a no-op → edit one page → exactly that page updates with version 2 and property recorded →
  manual-edit detection honoured/overridden → orphan reported not deleted → space mismatch refuses to write →
  audit entry appears on the Change Log page → gate fails then passes.

Add a project: put a markdown snippet in a test in `tests/test_docsync.py`; assert on the storage XML. The fake
server handles the v2 endpoints the tool uses; extend `FakeConfluence._req` if you add an endpoint.

## 2. Plugin wiring
```bash
claude plugin validate ~/git/docsync --strict      # manifests
claude plugin validate ~/git/docsync/skills          # skill frontmatter
claude plugin validate ~/git/docsync/agents
```
Try the skills against a demo repo without installing:
```bash
d=$(mktemp -d)/demo && mkdir -p $d && cd $d && git init -q -b main . \
  && python3 ~/git/docsync/scripts/docsync.py init --project-name Demo --space DEMO --root-page 1 --title-prefix "[Demo] " \
  && git add -A && git commit -qm init
claude --plugin-dir ~/git/docsync
> /docsync:status
```
Expected: the SessionStart hook prints the docsync reminder; `/docsync:status` lists 9 pages and 0 audit entries.

## 3. Dry run against real Confluence
```bash
cd <project-repo>
export CONFLUENCE_BASE_URL=… CONFLUENCE_EMAIL=… CONFLUENCE_API_TOKEN=…
python3 .docsync/bin/docsync.py doctor      # root title, space OK, current children
python3 .docsync/bin/docsync.py plan --out  # created/updated/unchanged/orphans; XML in .docsync/out/
```
Open one `.docsync/out/*.xml`, paste its contents into a scratch Confluence page via "Insert → Markup →
Confluence wiki/storage" to eyeball the rendering. Nothing is written by `plan`.

## 4. First live publish into a sandbox
1. Create a throwaway page "docsync sandbox" in a personal space. Temporarily set its id as `root_page_id`
   and the personal space key as `space_key` in `.docsync/config.yaml`.
2. `python3 .docsync/bin/docsync.py push --json` → 10 pages appear under the sandbox, each with the banner.
3. Run push again → all `unchanged`. Edit a page in Confluence, run again → `MANUAL EDIT DETECTED`, content
   restored. Delete a markdown file, run `plan` → `orphan` reported, page still there.
4. Point the config back at the real root and commit.

## 5. The CI gate
Open a PR that edits one `.sql` file and nothing else → `docsync / gate` fails with the file listed.
Run `/docsync:update` in the branch, commit → gate passes. Add label `docs-not-needed` → gate is skipped.

## 6. Local publish mode
On a repo with a real open PR and `gh auth login` done:
`python3 .docsync/bin/docsync.py merge-publish --pr <n> --dry-run` prints the documentation verdict and check
status without merging. Then `/docsync:merge <n>` on a throwaway PR against the sandbox root from section 4.

## 7. Backfill
On a project that already has Confluence pages under the root: `python3 .docsync/bin/docsync.py pull`, confirm
`.docsync/imported/index.json` lists exactly the pages under the root and nothing else. Run `/docsync:backfill`,
then check its outcome table: each "harvested" item should appear on a page and in `project-context.md`, each
"dead" item should be gone from the pages, and `grep -rn "TODO\|TBC" docs/confluence` should be empty.

## 8. Classification quality (the part that needs a human)
Take three past PRs from a real project: one refactor, one business-logic change, one path move. In a worktree at
each PR's head, run `/docsync:check --base <parent-sha>` and compare the verdict table with what you would have
decided. Tune `.docsync/project-context.md` and, if a rule is systematically wrong, `reference/classification.md`.
