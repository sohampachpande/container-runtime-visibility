# Milestone 5 plan of action

Decision date: 2026-10-27. Follow [the datastore decision](../docs/datastore.md).

```text
Tetragon: process execution + sensitive-file access + outbound TCP
  → Vector: common event envelope + typed event details + original JSON
      → local JSONL staging captures
          → completed batch → compressed Parquet
              → transactional import into events.duckdb
                  → saved SQL investigations
      → existing low-cardinality metrics
```

Parquet is the retained analytical batch format; native DuckDB tables are the
query store. Neither object storage nor a database server is introduced. This
extra persisted format supports inspection and replay; native DuckDB storage
already supports efficient analytics without requiring Parquet.

## Execution agreement and status

Implement one step at a time and validate before proceeding. The user authorized
two commits (docs/planning, then code) and then local Docker validation. Those
commits are `7eb4468` and `4cd602d`; subsequent schema and validation follow-ups
are documented below. No cloud changes or pushes. OOM collection is dropped;
preserve the original OOM exercise.

| Step | Deliverable | Current status |
|---|---|---|
| 1 | Three event categories and controlled raw-event probes | Passed on Desktop: policies enabled, execution correlation, Docker attribution, path/binary negatives |
| 2 | Uniform Vector schema and persistent staging output | Passed: 11 pinned-image tests, live all-category staging, exact raw preservation, bounded labels |
| 3 | Local Parquet conversion and native DuckDB ingestion | Core path passed: 10 offline tests, 591 live records, duplicate-safe retry, reopened DB, saved queries; retention/load/crash acceptance pending |

The storage foundation can be developed against the existing verified envelope
without guessing new kernel fields. This does not bypass step 1's live-event
gate for new mappings or establish milestone completion. See
[the batch ingestion guide](../docs/datastore-ingestion.md).

## 1. Enable and verify three kernel event categories

Keep the existing base sensor for process execution and exit. Add two
observation-only startup policies, mounted read-only in native Linux and
Desktop Compose:

- `runtime-sensitive-file`: `security_file_permission`, capturing file path,
  permission mask, and permission-hook return value for Node.js and the
  `/tmp/runtime-visibility-sensitive-` probe prefix.
- `runtime-network-connect`: `tcp_connect`, capturing the socket argument for
  `/nodejs/bin/node`. This binary filter is not container-specific: verify the
  exported Docker identity rather than claiming isolation to Juice Shop.

MVP boundary: both new policies exclude Python, other interpreter paths, and
tool subprocesses running different executables. The existing execution/exit
sensor remains independent of these filters. This is a policy-scope choice,
not a Tetragon language limitation. Broader interpreter and agent process-tree
coverage is follow-on work; see [the explanation and coverage table](../docs/security-events.md#why-the-mvp-filters-for-nodejs).

Use the Node.js probe to create fake credentials, read a protected and a control
path, and make a successful IPv4 TCP connection to its own loopback listener.
It reports expected paths, endpoint, and container PID, then removes its files.
Use an independent non-Node executable for a binary-filter negative probe.
Add refused-connection and IPv6 probes after the initial hook evidence is known.
Do not infer connection establishment, transferred bytes, or successful file
reads from hook entry alone. A permission return value is not the read result.

Run the existing process acceptance check, require both new policies to report
an enabled state, and capture raw events using [the event guide](../docs/security-events.md).
Match process execution IDs across process and kprobe events, compare workload
identity to Docker and live cgroup evidence, and confirm the control path is
excluded. A policy-loading error is a failed check, not an optional success.

**Gate:** actual raw records for all three categories, documented event semantics,
positive/negative filter checks, and no regression in execution/exit collection.
This gate passed on Docker Desktop on 2026-10-27. IPv6 and refused-connection
probes remain additional coverage beyond the successful IPv4 attempt probe.

## 2. Process events through a uniform Vector schema

Inspect captured JSON and the pinned Tetragon API before field mapping. Use one
shared envelope with schema version, event time in UTC, collector,
transport event type, bounded security category, host, optional process/parent/
container context, policy/hook identity, typed file/network details, and original
`tetragon` JSON. Missing optional fields remain null.

Use shared normalization and a small explicit mapping for the two known policy
names. File and network events can both arrive as `process_kprobe`; transport
type alone is insufficient to classify them. Extract details from the actual
captured argument structure, not guessed array positions or fixture-only fields.
Keep unknown valid payloads intact with bounded `other` classification or a
separate unsupported route; malformed records go to rejection output.

Add a persistent JSONL staging sink with an explicit schema version. Retain
console output for inspection and only bounded labels for metrics. Stable event
IDs are assigned during Parquet projection and must survive replay: derive them from canonical original event content if
no suitable native identifier exists; document that identical exported records
are deduplicated, not that every kernel action is uniquely identified.

Start with explicit capture completion: gracefully stop/flush Vector and publish
an immutable input file list. Do not rename a live file or assume a date change
prevents late writes. Continuous snapshot publication can follow later.

**Gate:** pinned Vector validation and meaningful transform tests for execution,
exit, file, network, optional absent context, malformed input, raw preservation,
category mapping, and label isolation. Fresh probes reach staged output with
exact original-object preservation. Compare the schema to all captured records.

## 3. Convert completed batches to Parquet and ingest DuckDB

Pin a DuckDB dependency and add one batch command with explicit input files,
Parquet output directory, and native database path, all outside Git. DuckDB
reads completed normalized JSONL with explicit common column types and writes
compressed Parquet; preserve the full normalized event and original payload in
JSON form alongside typed investigation columns. Never re-read mutable inputs.

Use the completed input digest as batch identity. Write to a temporary Parquet
file, validate row counts/schema, and publish atomically on the same filesystem.
Do not overwrite an already published batch. Reject invalid rows with a clear
failure/report rather than silently losing records.

Import published Parquet into native DuckDB tables in one transaction together
with an imported-batch ledger. Enforce stable event-ID deduplication across
batches. A retry after conversion or after database commit must not duplicate
rows; a rolled-back import must not leave the batch marked complete. Keep one
process responsible for read/write access to the native database. Future API
queries during ingestion belong in that process or require revisiting serving.

Provide SQL examples for execution history, protected-path access, and outbound
connections by workload/time. Keep Parquet retention and database retention
separate; propose seven days initially and measure disk growth before setting a
budget. Preserve source batches until validation succeeds and coordinate cleanup
with readers/imports. No claim of automatic expiry, crash durability, or exactly-
once delivery without testing it.

Resolve the prior Milestone 4 source-rotation/outage and acceptance-reader
findings before claiming recovery, and counter expiration before stable rate
behavior. These are focused prerequisites, not new collection features.

Follow-up configuration includes retained rotated `.log` files, reads new
files from the beginning, and disables exporter counter expiration. An isolated
pinned-image stop/rotation/restart test recovered both unread records without
replaying the checkpointed record; a 65-second idle check retained the counter.
The acceptance reader now compares
retained raw-record fingerprints rather than reapplying a byte offset after
rotation, and each probe gets a unique marker. Its live process acceptance check
passed. Collector-driven rotation under load and crash recovery remain untested.

**Gate:** tests for conversion types and counts, optional fields, raw JSON,
repeated inputs, overlapping batches, conversion/import failure, and rollback.
Live evidence must trace fresh events from each category through raw JSON,
Vector, Parquet, and DuckDB. Verify persistence, bounded recovery, retention,
resource use, and query latency. Mark unsupported or unverified cases explicitly.

## Completion and follow-on work

Update milestone evidence only after each gate passes. The user added an
optional [local UI](../docs/datastore-ui.md) on 2026-10-28: a separate container
reads Vector staging read-only, imports complete-line snapshots, and owns the
native database for charts and bounded SQL queries. This does not close the
outstanding retention/load/crash gates. Application API, alerts, broader file
coverage, inbound networking, UDP, and DNS attribution remain follow-on work.
ClickHouse remains the preferred future server if measured
shared-ingestion/query requirements justify it.

On 2026-11-01 the user approved [process-noise reduction](../docs/process-noise.md):
keep selected-workload lifecycle records before staging, default Explore to
Juice Shop, and group execution/exit by host and execution ID. Preserve raw
host-wide logs, file/network routing, original records, and historical storage.
Refresh the configured container ID after application recreation; automatic
workload discovery is not introduced. This does not change the outstanding
retention/load/crash acceptance gates.

Required follow-up: replace manually maintained instance IDs with stable
workload selection and automatic discovery of replacement/replica IDs. Surface
missing or stale discovery as unhealthy, and validate recreation/replica
coverage with fresh probes. The current acceptance script is a manual check,
not continuous discovery. See [the documented assumption and failure mode](../docs/process-noise.md#known-assumption-manually-maintained-container-identity).
