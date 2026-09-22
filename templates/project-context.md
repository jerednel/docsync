# Project context for documentation

<!-- This file is the brief the documentation agent reads before every update.
     Keep it current: it is the ONLY place the agent learns things that are not in the code.
     Plain sentences. Bullets. No prose essays. -->

## What this project is trying to achieve
<!-- 3–6 sentences. What decision or product does the data support? Who consumes it? -->

## Who is who
| Role | Name / team | Owns | Contact |
|---|---|---|---|
| Source data owner | | source systems, access | |
| 3rd-party data provider | | landing files to S3 | |
| Data engineering (us) | | pipelines, tables, this documentation | |
| Analytics | | reads marts, builds dashboards | |
| Data science | | models on top of marts | |

## Hand-offs and cadence
<!-- One row per thing that arrives or leaves. This feeds the "Who does what" page. -->
| What | From → To | Where it lands | How often | Deadline / SLA | Quality standard | Who fixes it when it breaks |
|---|---|---|---|---|---|---|
| e.g. Weekly sales extract | Provider → s3://bucket/raw/sales/ | | Mondays 06:00 UTC | by 09:00 UTC | schema v3, no nulls in `store_id` | provider ops |

## Environments and where things run
<!-- e.g. Snowflake account/db/schemas, S3 buckets per env, Airflow/dbt hosting, CI/CD. -->

## Conventions that are not obvious from the code
<!-- Naming (stg_/int_/fct_/dim_), logging and tracing rules, DQ test thresholds, timezone rules,
     currency, fiscal calendar, deduplication policy, late-arriving data policy. -->

## Known assumptions and business rules already agreed
<!-- Anything an analyst would be surprised by. e.g. "Returns are netted at day level",
     "Spend is allocated by impressions share", "Pre-2023 history excluded because..." -->

## Audience notes
<!-- Who reads the docs, what they typically need, and what confuses them today. -->

## Out of scope for documentation
<!-- Things the agent should NOT document (e.g. other teams' repos, upstream systems you do not own). -->
