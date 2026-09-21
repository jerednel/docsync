---
title: Storage locations
order: 50
---
<!-- Every bucket, path pattern and landing zone. Exact strings. Partition and naming patterns spelled out. -->

[TOC]

| Location | Pattern | Format | Partitioned by | Written by | Read by | Retention |
|---|---|---|---|---|---|---|
| `s3://<bucket>/raw/<source>/` | `dt=YYYY-MM-DD/<source>_<YYYYMMDD>.csv.gz` | CSV gzip | date | provider | ingestion | |

## Environments
| Env | Bucket / account | Notes |
|---|---|---|
| dev | | |
| test | | |
| prod | | |
