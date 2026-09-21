---
name: merge
description: Merge a PR and publish its documentation to Confluence from this machine (local publish mode, no CI secrets needed).
disable-model-invocation: true
argument-hint: "<pr-number|url> [--method squash|merge|rebase] [--delete-branch] [--force]"
allowed-tools: Bash Read
---
> **Paths.** A repo holds one docsync case per project. `<case_dir>` is `.docsync/cases/<case>/` (or `.docsync/` in a single-case repo) and holds `config.yaml`, `case-context.md`, `audit.jsonl`. `<docs_dir>` is the `docs_dir` in that config (default `docs/<case>/`, or `docs/confluence/` in a single-case repo). `python3 .docsync/bin/docsync.py status` lists the cases. With several, work on the case whose `watch_paths` match the change and pass `--case <case>` to every docsync command.


You merge on the user's behalf and publish immediately, so Confluence reflects trunk within a minute of the merge.
Merging is irreversible: verify first, then act once.

1. **Preconditions.** `gh auth status` succeeds; `env | grep -c CONFLUENCE_` is 3. If either fails, explain the fix and stop.
2. **Verify.** `python3 .docsync/bin/docsync.py merge-publish --pr <n> --dry-run`. It prints the PR, whether its file
   list satisfies the documentation rule (pipeline code changed ⇒ docs/audit changed, or label `docs-not-needed`),
   failing checks, and conflicts. Also `python3 .docsync/bin/docsync.py doctor` to confirm the Confluence root.
3. **Decide.** If the verify step reports FAIL or failing checks, stop and tell the user what is missing. Do not use
   `--force` unless the user wrote it in `$ARGUMENTS`.
4. **Merge and publish.** `python3 .docsync/bin/docsync.py merge-publish --pr <n> $ARGUMENTS --json`. This merges via
   `gh pr merge`, fetches trunk, checks out the merged commit in the trunk worktree (or a temporary detached one),
   publishes changed pages with the PR number and merge commit in the banner, regenerates the Change Log, and
   cleans up.
5. **Report.** Merge method and commit, then the created/updated/unchanged/orphan/manual-edit summary verbatim,
   then the Confluence root URL. If publish failed after the merge, say so clearly and give the retry command:
   `python3 .docsync/bin/docsync.py push --pr <n>` from a trunk checkout.
