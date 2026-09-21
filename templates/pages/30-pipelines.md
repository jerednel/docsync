---
title: Pipelines
order: 30
---
<!-- One section per pipeline/DAG/dbt project run. Same headings every time so readers know where to look. -->

[TOC]

## Pipelines at a glance
| Pipeline | Trigger / schedule | Reads | Writes | Owner | Logs |
|---|---|---|---|---|---|
| | | | | | |

## PIPELINE_NAME (repeat this section per pipeline)
- **Purpose:** one sentence.
- **Trigger and schedule:** cron / event / manual; timezone.
- **Inputs:** tables, paths (link to [Storage locations](50-storage-locations.md)).
- **Outputs:** tables (link to [Tables and datasets](40-tables-and-datasets.md)).
- **Steps and business logic:** numbered steps in plain English. Name the file for each step.
- **Assumptions baked in:** link each to [Assumptions and business rules](60-assumptions-and-business-rules.md).
- **What happens on failure:** retries, alerts, who is paged, how to rerun.
- **Where to look:** log location, dashboard, run history.
