# Datastore decision

Decision date: 2026-10-27.
Status: local JSONL staging, Parquet conversion, and native DuckDB tables are
selected. The schema-v1 batch importer passes 10 offline tests, and all three
categories passed local end-to-end validation on 2026-10-27. Retention cleanup
and sustained-load/crash recovery remain pending. ClickHouse is
the preferred next datastore if serving or ingestion requirements outgrow this
design. This supersedes the OpenSearch proposal.

## Workload

Store normalized process-execution, sensitive-file, and network-connection
observations from Tetragon after processing by Vector. OOM collection is outside
Milestone 5; the standalone OOM debugging exercise remains available.

The primary queries are analytical: event counts over time, uncommon executables,
protected-path access by workload, destination distributions, and correlation
by process execution identity. Paths and arguments are strings, but their
presence does not make full-text search the primary workload. Retain complete
original events alongside typed common fields for investigation.

Initial use is one developer on Docker Desktop with controlled probes. Event
volume, query latency, and storage growth have not been benchmarked. Shared
UI access, continuous alerting, and an application API are later requirements.

## Options considered

| Option | Strengths for this project | Tradeoffs | Decision |
|---|---|---|---|
| DuckDB | Embedded SQL analytics; JSON import, Parquet conversion, and native tables without a server | Batch publication/import, retention, and database ownership remain our responsibility | Selected with Parquet batches and native tables |
| ClickHouse | Column-oriented OLAP server; direct Vector sink; a natural path to continuous ingestion and shared SQL queries | Adds a service, table design, resource tuning, and lifecycle management before we have measured demand | Preferred next step when needed |
| OpenSearch | Fielded/full-text search, Dashboards, and query-based alerts | Index mappings and search-cluster operations are additional work; built-in investigation UI is not our immediate priority | Not selected |

OpenSearch can aggregate structured events; it is not limited to text search.
The decision favors our SQL analytical workflow, rather than excluding a
capability OpenSearch possesses. ClickHouse is an appropriate engine, but a
server deployment is unnecessary for the current capture-and-query workflow.
It could become simpler than extending DuckDB once live serving is required.

Sources: [DuckDB JSON](https://duckdb.org/docs/stable/data/json/overview),
[ClickHouse OLAP overview](https://clickhouse.com/docs/get-started/about/intro),
[Vector ClickHouse sink](https://vector.dev/docs/reference/configuration/sinks/clickhouse/),
[OpenSearch Discover](https://docs.opensearch.org/latest/dashboards/discover/index-discover/),
and [OpenSearch alerting](https://docs.opensearch.org/latest/observing-your-data/alerting/index/).

## Finalized initial architecture

```text
Tetragon JSON
  → Vector: validate, normalize, retain original event, route
      → persistent JSONL staging files
          → completed immutable batch
              → DuckDB conversion → compressed Parquet
                  → transactional import → native events.duckdb tables
                      → SQL investigations via CLI/Python
      → existing low-cardinality metrics
```

JSONL means one JSON event per line. Vector writes staging files, DuckDB creates
retained Parquet batches, and a transactional loader imports those batches into
native DuckDB tables. These are three local representations with distinct
roles, so their combined disk cost must be measured. No object storage or HTTP
ingestion bridge is introduced. Vector's local file sink writes normalized
JSON separately from its console diagnostics; Parquet conversion is a batch
command, not an assumed local Vector codec.
[Vector file sink](https://vector.dev/docs/reference/configuration/sinks/file/)

Start with completed captures: finish a probe, gracefully stop/flush the writer,
then convert an explicit file list. Do not convert a file while it is being
appended or rename it while Vector still has it open. A later continuous workflow must
explicitly publish immutable batches or snapshots. A date-based filename or
idle close alone does not establish immutability: late events can reopen it.
Record each query's capture boundary; initial results are batch-oriented, not
a real-time view of every incoming event.

DuckDB reads JSON with explicit common column types to produce Parquet. Retain
the complete normalized event, including original Tetragon data, as JSON beside
typed investigation columns. The initial importer pins DuckDB 1.4.1 and supports
the schema-v1 envelope. Vector maps live-verified file/network details into
the preserved JSON; typed common columns support time/workload queries. See
[the implementation, measurements, and commands](datastore-ingestion.md).
[DuckDB JSON loading](https://duckdb.org/docs/stable/data/json/overview)

Parquet is selected for retained, portable analytical batches and replay into
DuckDB or a future ClickHouse deployment. It is not required to make native
DuckDB tables fast: native storage already supports analytical queries.
Conversion adds CPU work, publication/checkpoint logic, and another copy of the
data. Measure those costs; do not promise a speedup over native DuckDB tables.
[DuckDB Parquet support](https://duckdb.org/docs/stable/data/parquet/overview)

## Data contract and operational boundaries

- Preserve event time in UTC, schema version, security category, collector event
  type, host identity, execution identity, and available process/workload context.
  Include file operation/path and network endpoint/outcome fields when present.
- Keep the original Tetragon object. Missing identity remains null; do not
  substitute a PID or Docker ID prefix for a cgroup identity.
- Establish stable event identity for replay/deduplication where possible.
  Do not use PID alone or an ingestion-time random ID as a replay identity.
  Document duplicate behavior; no exactly-once guarantee is assumed.
- Keep events, snapshots, temporary query files, and any DuckDB database outside
  Git. Record counts and compare captured events to SQL results in acceptance.
- Configure a disk budget, retention cleanup, and full-disk behavior explicitly.
  Start with a proposed seven-day retention window; measure bytes per day before
  finalizing the budget. Protect published batches for the duration of queries
  and coordinate cleanup so active query inputs are not removed.
- Validate flush/restart behavior and bounded recovery. A successful file write
  or persistent mount alone does not establish crash durability or recovery
  across upstream log rotation. Resolve the Milestone 4 recovery findings before
  making those claims.

For the selected persistent native DuckDB database, embedded read/write access
must be owned by one process; other processes must not independently open that file
for queries while the writer runs. DuckDB also documents remote/shared-storage
options, but these are outside the chosen embedded design. Start with serial
batch import and CLI queries after the importer closes its connection. Record
the imported-batch digest in the same transaction as event inserts; retries and
overlapping batches must not duplicate rows. Publish Parquet atomically after
validation, and preserve inputs on failure. Test conversion, rollback, and
recovery rather than assuming exactly-once processing.
[DuckDB concurrency](https://duckdb.org/docs/stable/connect/concurrency)

## UI, API, and alerts

The optional [local dashboard](datastore-ui.md) runs DuckDB and Streamlit in a
separate container. It reads Vector's volume read-only, imports immutable
complete-line snapshots on demand, and owns its database on a separate volume.
It supplies filters, charts, event inspection, and a read-only SQL editor.
The original host-side batch workflow remains supported for separate databases.

A future local API can own the DuckDB database and serve queries alongside batch imports,
returning bounded, paginated results. We must implement its access controls and query scheduling;
DuckDB does not supply the project's API or alert service merely by being used.

Scheduled SQL checks can later produce explainable findings, with scheduling,
state, duplicate-alert handling, and notification delivery designed separately.
Reconsider ClickHouse before building a continuously ingesting shared service
around an increasingly complex DuckDB deployment.

## When to move to ClickHouse

Revisit the decision when any of these becomes a measured requirement:

1. Continuous ingestion and fresh queries must run concurrently for several
   consumers beyond the chosen single-process database ownership model.
2. Multiple hosts send events to a shared datastore.
3. Representative time-window queries miss the agreed latency target after
   testing bounded queries and sensible Parquet layout.
4. Retention, disk use, or sustained ingestion exceeds the local machine's budget.
5. The custom batching/serving infrastructure needed for DuckDB becomes more
   complicated than operating one ClickHouse server.

There is no unmeasured row-count threshold or requirement to build a cluster.
Benchmark first; ClickHouse can initially be a single server.

Keep Vector's normalized event contract independent of storage. For migration,
create explicit ClickHouse tables and choose order/partition keys from real
queries, validate the pinned Vector ClickHouse sink, and backfill retained
files. Define a cutover boundary and test duplicates, field types, timestamps,
counts, and representative query results before switching consumers. SQL and
JSON type behavior are not identical between the engines; migration is not a
drop-in connection-string change.

## Milestone 5 storage acceptance

Trace one fresh probe per category through raw JSON, normalized staging output,
Parquet, and native DuckDB tables. Verify persistence, completed-capture
boundaries, schema and original-payload preservation, malformed input isolation,
duplicate-safe imports, rollback, retention of each representation, and bounded
outage recovery. Record capture size, conversion/import/query duration, peak
memory, and freshness. The optional local UI is implemented separately; shared
serving, a custom API, and alerting remain follow-on work.
