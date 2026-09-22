> **Paths.** A repo holds one docsync project per team or workstream. `<project_dir>` is `.docsync/projects/<project>/` (or `.docsync/` in a single-project repo) and holds `config.yaml`, `project-context.md`, `audit.jsonl`. `<docs_dir>` is the `docs_dir` in that config (default `docs/<project>/`, or `docs/confluence/` in a single-project repo). `python3 .docsync/bin/docsync.py status` lists the projects. With several, work on the project whose `watch_paths` match the change and pass `--project <project>` to every docsync command.

# Page model: where each kind of fact lives

The documentation is a fixed set of pages under one Confluence root. Each markdown file in
`<docs_dir>/` is one page. Titles come from frontmatter; the configured `title_prefix` is added on publish.

| Page (file) | Holds | Never holds |
|---|---|---|
| Start Here (`00-start-here.md`) | One-paragraph purpose, the "I want to know… → go to" map, who to ask | Any detail. Link out. |
| Who does what (`10-who-does-what.md`) | Every hand-off: what, who, where, how often, deadline, quality bar, escalation | Column definitions, logic |
| Data lineage (`20-data-lineage.md`) | Layers, hop-by-hop flow, which pipeline builds which table | Schedules, owners |
| Pipelines (`30-pipelines.md`) | Per pipeline: trigger, inputs, outputs, steps in plain English, assumptions (linked), failure behaviour, logs | Column-level detail, path patterns (link) |
| Tables and datasets (`40-tables-and-datasets.md`) | Per table: grain, keys, refresh, source, use/don't use, columns | How it is computed step by step (link to Pipelines) |
| Storage locations (`50-storage-locations.md`) | Every bucket/prefix/pattern, format, partitioning, writer, reader, retention, per environment | Business meaning of the data |
| Assumptions and business rules (`60-assumptions-and-business-rules.md`) | Numbered rules: rule, why, where implemented, owner, since, status | Anything that is not a decision about meaning |
| Engineering conventions (`70-conventions.md`) | Naming, layout, logging/observability/tracing, DQ tests, environments, local runs | Project-specific business rules |
| Glossary (`80-glossary.md`) | One line per term | Multi-paragraph explanations |
| Change Log (generated) | Rendered from `<project_dir>/audit.jsonl`; never edited by hand | — |

## Finding the home for a change
1. Identify the entity: table, pipeline, path, rule, owner, term, convention.
2. Use the table above. If two pages qualify, the fact goes on the more specific one; the other page links to it.
3. `grep -ril "<entity>" <docs_dir>/` to find every mention. Fix all of them so no page contradicts another.

## Adding a page
Only when an entity class does not fit anywhere above (for example an external data contract).
Create `<docs_dir>/NN-slug.md` with frontmatter:
```
---
title: Human title
parent: Pipelines        # optional; title of an existing page
order: 35
---
```
Add a row to the Start Here map. Titles must be unique within the docs dir.

## Removing a page
Delete the file. `docsync push` reports the Confluence page as an **orphan** and never deletes it.
Archive it in Confluence by hand and mention this in the PR.
