# Toronto Crime Patrol Planning Pipeline

A production-style data pipeline built on Azure, ingesting live Toronto Police crime data and transforming it into patrol-planning insights — built to learn and demonstrate the exact Azure stack (ADF, Databricks, ADLS Gen2, Azure DevOps) used by Azure-based DevOps/BI platform teams.

**Live data source:** [Toronto Police Major Crime Indicators API](https://data.torontopolice.on.ca) (~474,000+ real incidents)

---

## Why this project exists

I came into this with hands-on AWS/GCP experience but no production Azure work. Rather than just study Azure concepts, I built a real, working pipeline on the exact stack a target role required — so I could speak from experience, not theory, and so I'd hit (and have to solve) the same real constraints a production engineer hits.

---

## Architecture

```
Toronto Police API
       |
       v
ADF (scheduled trigger)
  - Web activity: calls the live API
  - Copy activity: lands raw response into ADLS bronze/
  - Web activity: triggers Azure DevOps pipeline via REST API
       |
       v
ADLS Gen2 (data lake)
  bronze/  ->  silver/  ->  gold/
  (raw)        (cleaned,     (patrol-planning
               validated,     + Power BI ready
               deduped)       tables)
       |
       v
  Power BI Dashboard
  - Map of incidents by type
  - Neighbourhood x hour hotspot matrix
  - Citywide hourly trend

Cross-cutting:
- Azure DevOps: CI/CD — Dev (auto) -> manual approval -> Prod
- Secrets: Azure DevOps secure variable group (masked)
- Lifecycle policy: auto-deletes bronze/silver after 7 days
```

**[Architecture diagram image goes here — export from the chat and add to repo as `docs/architecture.png`]**

---

## Why I built it this way (decisions & tradeoffs)

### Why three independent scripts instead of one notebook
`bronze.py`, `silver.py`, and `gold.py` are separate, and each reads its input from ADLS storage rather than from a shared Python variable. This means each stage is independently retryable, schedulable, and debuggable — if silver fails, I can fix and re-run it without re-fetching bronze. In a real environment, this is exactly the shape ADF would orchestrate as three chained activities. I deliberately designed for that handoff even though I couldn't fully wire it (see "Known limitations" below).

### Why a live API instead of a static dataset
I started with a static historical CSV, then switched to live data specifically so I could prove the *scheduling and automation* parts of the stack actually work — a static file doesn't give you anything meaningful to schedule. The live Toronto Police API also let me build and verify a genuine duplicate-detection step, since polling on a schedule produces real overlapping pulls.

### Why the medallion (bronze/silver/gold) pattern
Bronze keeps an untouched, auditable copy of every API response — important in a justice/policing context where you want a defensible record of exactly what was received. Silver applies validated, explicit cleaning rules. Gold is shaped specifically for the consumption pattern (Power BI, patrol planning), not for storage efficiency — e.g. the fact table stays at full grain rather than pre-aggregated, so the dashboard can drill into any dimension rather than being locked into pre-computed slices.

### Why `occurrence_hour` instead of deriving an hour from a timestamp
This was a real bug I found and fixed (detailed below) — worth a dedicated section since it's the best example of validating data, not just moving it.

---

## A real bug I found and fixed

While building the hourly-trend visual, the result was nonsensical — almost every incident showed the same hour. I traced it back to the source: both `REPORT_DATE` and `OCC_DATE` in the Toronto Police API are date-only values with the time zeroed out (effectively a daily snapshot timestamp, not a per-incident time). Deriving "hour of day" from either field was always going to produce a near-constant result.

The fix: the API separately exposes a purpose-built `OCC_HOUR` field (a genuine 0–23 integer, confirmed with real per-incident variation in raw data). I added it into `silver.py`'s schema validation and type conversion — including a new sanity check flagging any value outside 0–23 — and changed `gold.py` to use it directly instead of deriving an hour from any date field.

**Verified result:** the hourly trend table went from collapsing onto 1–2 hours to a full, realistic 24-hour spread, with a clear pattern — peak incidents around midnight, lowest around 5–7am, climbing again through the evening.

This is the kind of validation step that's easy to skip: a pipeline can run successfully end-to-end and still produce meaningless output if you don't sanity-check what the numbers actually mean.

---

## Production-grade silver layer

Eight explicit data-quality controls, not just formatting:

1. **Schema validation** — fails loudly if the source API changes shape, rather than silently producing partial/wrong data
2. **Explicit rename to snake_case** — a stable contract for downstream consumers, independent of the source API's naming
3. **Type coercion with `errors="coerce"`** — malformed values become null instead of crashing the run
4. **Per-field missing-value policy** — e.g. missing `premises_type` becomes `"Unknown"` (kept); missing `event_id`/`offence`/`division`/`report_date` causes the row to be dropped (and counted in the log)
5. **Duplicate detection** — dedup on `event_id`, necessary because the scheduled API poll can return overlapping windows
6. **Outlier/sanity checks** — flags future-dated records and out-of-range lat/long or hour values instead of silently trusting the source
7. **Data quality flag column** — flagged records are kept and labeled, not silently deleted, supporting auditability
8. **Lineage/audit metadata** — every row stamped with its source and ingestion timestamp for traceability

---

## CI/CD pipeline (Azure DevOps)

- **Trigger:** any commit to `main`, plus a daily scheduled cron trigger
- **Dev stage:** installs dependencies, then runs `bronze.py` → `silver.py` → `gold.py` as three separate, separately-logged steps
- **Prod stage:** gated behind a real manual Approval check on the `production` environment — verified twice, including catching and fixing a case where the approval gate silently stopped enforcing after a config change

**Secrets:** stored in an Azure DevOps variable group (`TORONTO_API_URL`, `STORAGE_ACCOUNT_NAME`, `STORAGE_ACCOUNT_KEY`), masked, injected via YAML `env:` blocks, read in Python via `os.environ.get(...)`. None appear in committed code.

**A real bug I hit and fixed:** the ADF activity that triggers the Azure DevOps pipeline initially failed with a .NET error ("Misused header name"), caused by manually setting a `Content-Type` header on a Web activity — which conflicts with how .NET's HttpClient classifies request vs. content headers. Fixed by removing the header and letting ADF infer it from the JSON body.

---

## ADF orchestration

```
Web1 (call Toronto Police API)
   -> Copy data (write response into bronze/crime_incidents/)
      -> Web2 (POST to Azure DevOps REST API, queuing a new pipeline run)
```

ADF doesn't just call the API — it lands the data and then directly triggers the CI/CD pipeline via Azure DevOps's REST API, so the bronze→silver→gold chain is event-driven off real data arrival, not a guessed time offset.

---

## Known limitations (and what I'd do differently in production)

I'm including this section deliberately — a real engineer names constraints rather than hides them.

- **Key Vault is provisioned but not linked to the pipeline.** Azure DevOps's automatic service-connection setup requires creating a service principal in Microsoft Entra, which student-subscription permissions block. Secrets are instead stored in an Azure DevOps masked variable group — same core property (out of code, masked, injected at runtime), different mechanism. In production, I'd link Key Vault directly for centralized audit logging and rotation.
- **ADF doesn't trigger Databricks directly.** My Azure Databricks workspace has zero CPU-core quota on the student subscription (a known restriction, not something fixable from my side). I built and validated the transformation logic in Databricks Free Edition (serverless, no cluster needed) and run the production version as independent Python scripts in Azure DevOps instead.
- **Power BI Desktop requires a manual refresh.** The underlying Azure data pipeline is fully automated and refreshes daily without any action from me. The `.pbix` report file itself doesn't auto-refresh unless published to the Power BI Service with a scheduled refresh — which requires a Pro license tier I haven't set up for this project.

---

## Tech stack

| Layer | Tool |
|---|---|
| Storage | Azure Data Lake Storage Gen2 |
| Orchestration / scheduling | Azure Data Factory |
| Transformation | Python (pandas), developed in Databricks Free Edition |
| CI/CD | Azure DevOps Pipelines (YAML, multi-stage) |
| Secrets | Azure DevOps variable groups (Key Vault provisioned, not yet linked) |
| Visualization | Power BI Desktop |
| Source control | Git (Azure DevOps Repos, mirrored here) |

---

## Repository structure

```
.
├── notebook/
│   ├── adls_helpers.py      # shared ADLS connection + read/write helpers
│   ├── bronze.py            # API extraction -> raw landing
│   ├── silver.py            # validation, cleaning, dedup
│   └── gold.py               # patrol-planning + Power BI-ready tables
├── azure-pipelines.yml       # multi-stage CI/CD pipeline
├── docs/
│   ├── architecture.png      # architecture diagram
│   └── dashboard-screenshots/
└── README.md
```
