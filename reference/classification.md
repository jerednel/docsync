# Classification rubric: does this change belong in the documentation?

Work in **logical changes**, not files. A PR that edits five files to move one dedup rule is one logical change.
Every logical change touching watched code gets exactly one audit entry: `documented` or `excluded`.

## Documentable (verdict: documented)

| Category | Trigger | Home page |
|---|---|---|
| `business-logic` | A filter, join, aggregation, calculation, default, dedup or windowing rule changed so the same input produces different rows or values. Includes changed thresholds, date cut-offs, currency/timezone handling, null handling. | Assumptions and business rules; the affected section in Pipelines; the affected table in Tables |
| `storage-path` | A bucket, prefix, path pattern, partition scheme, file naming or file format changed or was added. | Storage locations; Who does what (if a hand-off lands there) |
| `tables` | A table/view/model was added, removed, renamed, re-grained, or had columns added/removed/redefined. Includes schema moves. | Tables and datasets; Data lineage |
| `new-feature` | A new dataset, pipeline, model, source, or output that a PM, analyst or scientist would use or ask about. | Start Here (map), Pipelines, Data lineage, Tables |
| `ownership-process` | Who delivers what, cadence, SLA, deadline, escalation path, environment ownership changed. | Who does what |
| `data-quality` | DQ tests, thresholds, quarantine rules, acceptance criteria for incoming data changed. | Who does what (quality bar); Pipelines (failure behaviour) |
| `conventions` | Naming, logging, tracing, observability, testing or deployment conventions changed in a way a new engineer must know. | Engineering conventions |

## Not documentable (verdict: excluded)

- Refactors: same inputs produce the same outputs (extract function, rename internal variable, reorder, type hints, dead code removal).
- Formatting, linting, comments, docstrings.
- Test-only changes, fixtures, mocks.
- Dependency or runtime version bumps with no behavioural change.
- Infrastructure changes that do not alter where data lives, when it runs, who owns it, or how it fails: runner sizes, retry counts within the existing SLA, IAM tidy-ups, CI plumbing.
- Changes to the documentation or docsync tooling itself.

## Grey-zone tests

Ask each. Any **yes** makes the change documentable.

1. Would an analyst's existing query return different rows or values after this change?
2. Would someone following the current docs look for a file, table or column in the wrong place?
3. Would the owner, schedule, deadline or quality bar in "Who does what" now be wrong?
4. Would a new engineer be surprised by this after reading "Engineering conventions"?
5. Does it introduce something a PM would ask "what is this?" about?

Still unsure: mark `documented` with the smallest honest edit. A one-line fix beats a stale page.

## Audit entry shape

```json
{
  "summary":   "Dedup fct_sales on (order_id, line_id) instead of order_id",
  "category":  "business-logic",
  "verdict":   "documented",
  "rationale": "Row counts in fct_sales change; totals were previously overstated for multi-line orders",
  "files":     ["dbt/models/marts/fct_sales.sql"],
  "pages":     ["Assumptions and business rules", "Tables and datasets"]
}
```
`pages` uses the page titles without prefix. Excluded entries omit `pages` and put the reason in `rationale`.
Record with:
```bash
python3 .docsync/bin/docsync.py audit add --json '<entry>'
```
