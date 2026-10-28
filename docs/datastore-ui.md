# Local query dashboard

The optional `datastore-ui` container runs Streamlit 1.50.0 and embedded DuckDB
1.4.1. DuckDB is a library in this container, not a service running inside
Vector. The original host-side importer remains usable for separate captures.

```text
Vector container
  → vector-data volume: /var/lib/vector/staging/*.jsonl
      → read-only mount in datastore-ui: /source/staging/*.jsonl
          → Import latest: immutable complete-line snapshots
              → Parquet batches → events.duckdb → charts / events / SQL
                 all stored on the UI's own datastore-data volume
```

Vector owns its staging files and checkpoints. The UI reads only the staging
subdirectory; its mount of the existing volume is read-only. It has no Docker
socket, host PID namespace, or Tetragon privileges. The UI is the only process
that owns this native database. A shared process lock serializes imports and
queries, including requests from multiple browser sessions. Do not run another
importer process against this database while the UI is running.

This follows [DuckDB's single-process read/write model](https://duckdb.org/docs/stable/connect/concurrency).
Sharing JSONL inputs between containers is suitable here; sharing a mutable
DuckDB file between independent writers/readers would need different ownership.

## Start on Docker Desktop

Run from the repository root. The `ui` profile keeps the extra service optional;
ordinary Compose startup still starts the three telemetry/demo services.

```sh
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml --profile ui config --quiet
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml --profile ui \
  up --detach --build datastore-ui
```

Open [the dashboard](http://127.0.0.1:8501) and choose **Import latest**.
It reads the existing staging files; Vector does not need to stop. To generate
new controlled activity first, run:

```sh
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml exec -T juice-shop \
  /nodejs/bin/node < scripts/probe-security-events.js
```

The application stays bound to loopback. It has no application authentication
and is intended for a trusted local developer. Do not expose it publicly.
Process arguments, file paths, and original events can contain sensitive data.

## Explore and query

- **Import latest** adds new records, preserving the existing importer’s
  event-ID deduplication. Repeated inputs do not duplicate native table rows.
- **Explore** filters by time window, security category, and Docker ID prefix.
  Time windows are relative to the latest imported event, so old captures stay
  useful. All displayed times are UTC. A Docker prefix is not a full ID or cgroup.
- Category counts, an event timeline, and top TCP destinations summarize the
  selection. The timeline shows at most 500 time/category groups; the table
  shows the latest 200 matching events. Select a row for the full normalized
  object, including its original `tetragon` event.
- **SQL** runs one read-only `SELECT`, with up to 500 returned rows and a
  five-second execution timeout. SQL has its own filters; Explore filters do
  not apply. File/network access and automatic extension loading are disabled
  for query connections. Database mutations and multiple statements are rejected.

The flattened `security_events` view exposes common fields and verified details:
`event_time`, `category`, `event_type`, `host_name`, `process_exec_id`,
`container_id_prefix`, `executable`, `arguments`, `file_path`, `permission_mask`,
`permission_result`, `destination_ip`, `destination_port`, `socket_state`,
`event_id`, and `normalized_event`.

```sql
SELECT destination_ip, destination_port, count(*) AS attempts
FROM security_events
WHERE category = 'network'
GROUP BY destination_ip, destination_port
ORDER BY attempts DESC;
```

File records are permission-hook observations, potentially repeated or for
directories; a return value of zero does not prove a completed read. TCP records
are outbound attempts, not established sessions or transferred bytes. The
Node.js/probe-path collection boundaries still apply. Older version-1 captures
without category fields appear under `other`; the UI does not invent coverage.

## Snapshot boundaries and tradeoffs

Vector appends one event per newline. Each import opens a staging file, records
its initial byte size, copies complete newline-terminated records within that
boundary, and publishes an immutable snapshot on the UI volume. A trailing
partial record waits for the next import. New appends after the boundary also
wait. Ordinary truncation/replacement detected during capture fails the import;
the source files must retain Vector's append-only behavior.

The existing importer then validates this completed snapshot, atomically
publishes Parquet, and transactionally inserts records with a batch ledger.
Malformed complete rows fail instead of being silently skipped. Imports are
transactional per file, not across every file in one button click; earlier
completed batches remain if a later file fails. Retry is duplicate-safe.

This explicit snapshot step replaces the stop/flush/copy boundary for the UI
workflow. It does not make arbitrary active files safe for the batch CLI.
The original [host-side ingestion instructions](datastore-ingestion.md) still
require completed captures.

The small implementation copies the complete prefix again after a file grows.
Unchanged snapshots reuse their content-digest filename, but growing inputs
produce overlapping snapshots and Parquet batches. This costs CPU and disk;
use manual imports for this lab, not frequent unattended polling. It does not
provide live tailing, automatic expiry, a disk quota, or power-loss durability
guarantees. Incremental immutable batch publication is the next step if manual
snapshots become costly.

The service has a provisional 512 MiB container limit; query connections use
one DuckDB thread and a 128 MB engine limit. Existing conversion uses its own
256 MB engine limit. These limits are not total process-memory guarantees.
The optional service is for Desktop; remeasure available RAM before enabling
it on the small GCP VM. No cloud resource size is changed.

## Stop and preserve data

```sh
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml --profile ui stop datastore-ui
```

Stopping or recreating the container preserves `datastore-data`. Do not use
`down -v` if you want to retain the database, snapshots, Parquet, and existing
telemetry volumes. Retention cleanup remains pending.

## Verification

On Docker Desktop ARM64, 2026-10-28:

- The optional Compose profile builds and starts without changing the running
  collector, Vector, or Juice Shop services. The UI health check passes.
- All 18 tests pass in the built image: existing importer tests, immutable
  prefix snapshots, partial-tail deferral, growth/retry/reopen behavior, all
  category fields, read-only/file-access restrictions, row caps, a real query
  timeout with a successful following query, and Streamlit import/SQL actions.
- A fresh security probe reached the UI via the shared volume: 8 file checks
  and 1 TCP attempt appeared alongside process records. Data survived UI
  container recreation. The UTC timeline, category filter, and event table
  were inspected in the browser.
- One memory sample was about 125 MiB for the UI, within its 512 MiB limit.
  Vector, Tetragon, and Juice Shop remained within their existing limits.

Run the complete suite with the built image and a read-only repository mount:

```sh
docker run --rm --memory 512m --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount type=bind,source="$PWD",target=/workspace,readonly \
  --workdir /workspace --env PYTHONPATH=/workspace \
  --env PYTHONDONTWRITEBYTECODE=1 \
  runtime-visibility-desktop-datastore-ui \
  python -m unittest discover -s tests -v
```

These checks do not establish sustained ingestion capacity, full-disk behavior,
power-loss recovery, multi-host access, or a deployment suitable for public use.
