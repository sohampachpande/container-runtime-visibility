# Local Parquet and DuckDB ingestion

The batch importer accepts the current Tetragon `schema_version: 1` envelope,
not raw Tetragon logs or mixed Docker diagnostics. Fresh process, file, and TCP
records passed through Vector, staging, Parquet, and reopened DuckDB on
2026-10-27. Retention cleanup and sustained-load/crash recovery remain pending. Follow
[the action plan](../plans/milestone-5-datastore-plan.md) and
[the datastore decision](datastore.md).

These commands describe the host-side CLI workflow. The optional
[containerized dashboard](datastore-ui.md) instead reads Vector's shared volume
and creates completed snapshots when you choose **Import latest**; it owns a
separate database and does not require a host Python environment.

## What is implemented

1. Vector's `staging` file sink writes normalized process/file/network events to
   `/var/lib/vector/staging/events-%Y-%m-%d.jsonl` in its persistent data volume.
   Pinned-image validation and all 11 transform tests passed.
2. `scripts/ingest_events.py` validates one completed JSONL file and projects
   explicit column types. It snapshots projected rows into a private temporary
   directory so conversion does not repeatedly read the original file.
3. DuckDB writes Zstandard-compressed Parquet. The source file's SHA-256 names
   the batch. Full row comparison validates the output before atomic publication
   without overwriting an existing batch. A retry validates the existing file.
4. The importer inserts events and a batch ledger record in one transaction.
   Event IDs hash canonical original Tetragon JSON, making key order irrelevant.
   Identical exported records deduplicate within and across batches; this is
   record deduplication, not a promise of one row per unique kernel operation.

Parquet retains the source row count, including repeated records. DuckDB's
`events` table contains unique record IDs. If the same raw record arrives with
different normalized context, the first imported representation is retained.
There is no automatic schema migration or update of already imported rows.

| Column | DuckDB type | Meaning |
|---|---|---|
| `event_id` | VARCHAR, primary key in `events` | SHA-256 of canonical original event JSON |
| `event_time` | TIMESTAMPTZ | Event timestamp converted to UTC |
| `schema_version` | BIGINT | Current supported envelope version: 1 |
| `event_type` | VARCHAR | Collector event type |
| `host_name` | VARCHAR | Exported host name |
| `process_exec_id` | VARCHAR, nullable | Available execution identity |
| `container_id_prefix` | VARCHAR, nullable | Available Docker ID prefix |
| `normalized_event` | JSON | Complete normalized object, including original `tetragon` |

The typed `TIMESTAMPTZ` column has microsecond precision; the original timestamp
string, including any nanoseconds, remains unchanged in `normalized_event`.
Order events with execution identity as well as time when sub-microsecond
ordering matters. TIMESTAMPTZ represents an instant; its display timezone is
controlled by the querying session.

Vector normalizes verified file/network fields; the importer preserves them in
`normalized_event`. Common investigation columns are typed separately. Saved
[SQL investigations](../queries/security-events.sql) query category, execution
history, protected paths, and outbound endpoints without duplicating the schema
mapping inside the importer.

## 1. Create an isolated Python environment

Run from the repository root in one shell. These commands install only the
pinned DuckDB and timezone dependencies into a fresh temporary virtual environment; they do
not install host services or change Docker/cloud resources.

```sh
datastore_env=$(mktemp -d /tmp/runtime-visibility-python.XXXXXX)
python3 -m venv "$datastore_env"
"$datastore_env/bin/python" -m pip install -r requirements-datastore.txt
PYTHONDONTWRITEBYTECODE=1 "$datastore_env/bin/python" -m unittest discover -s tests -v
```

Python 3.9 and DuckDB 1.4.1 were used for the offline tests. The dependency
does not install a separate DuckDB database server. Keep these shell variables
available for the following commands.
`pytz` is needed when Python fetches a `TIMESTAMPTZ` value; it is not installed
automatically by DuckDB. See [DuckDB's conversion documentation](https://duckdb.org/docs/current/clients/python/conversion).

## 2. Validate and collect normalized output

First finish the policy-loading checks in [the security-event guide](security-events.md).
The commands below validate and test Vector, then update its service so the new
file sink is active. Startup may recreate services to apply pending configuration.
The existing acceptance script only proves execution/exit telemetry.

```sh
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml run --rm --no-deps vector \
  validate --no-environment /etc/vector/vector.toml
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml run --rm --no-deps vector \
  test /etc/vector/vector.toml
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml up --detach vector
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml restart vector
python3 scripts/verify-pipeline.py --desktop
```

The explicit restart applies changed contents of the bind-mounted configuration;
`up` alone can leave an existing Vector process using its old configuration.
Then run [the controlled security probe](security-events.md#3-generate-controlled-activity-in-another-terminal)
to produce file and TCP records as well as process execution.

## 3. Complete and copy a batch

Gracefully stop Vector to close/flush its staging files, copy them into a fresh
local directory, then start Vector again. The copied files are the completed
inputs. Do not convert the live named-volume files while Vector appends to them.
The pause creates an ingestion gap. An isolated controlled-rotation test passed;
recovery is still bounded by retained source files, and collector-driven rotation
under sustained load remains unverified. No volumes are removed.

```sh
datastore_data=$(mktemp -d /tmp/runtime-visibility-data.XXXXXX)
printf 'Local data directory: %s\n' "$datastore_data"
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml stop --timeout 30 vector
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml cp \
  vector:/var/lib/vector/staging/. "$datastore_data/staging"
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml up --detach vector
```

Check that the copy succeeded and contains JSONL files. If copying fails,
restart Vector before investigating. Preserve the copied files as immutable
inputs. A date-based filename alone does not make an active file immutable.

## 4. Convert and import

The importer processes one completed file at a time. This loop imports each
copied daily file into the same database. Repeating it should report
`already_imported` with zero newly inserted rows. Keep the source copies.

```sh
for capture in "$datastore_data"/staging/*.jsonl; do
  "$datastore_env/bin/python" scripts/ingest_events.py "$capture" \
    --parquet-dir "$datastore_data/parquet" \
    --database "$datastore_data/events.duckdb" || break
done
```

Run imports serially. Do not open the mutable database in another process while
the importer owns it. If database import fails after Parquet publication, the
Parquet remains available for retry; malformed input does not publish a partial
batch. The command reports counts and paths rather than event contents.

After the importer closes, inspect counts through a read-only connection:

```sh
"$datastore_env/bin/python" - "$datastore_data/events.duckdb" <<'PY'
import sys
import duckdb
with duckdb.connect(sys.argv[1], read_only=True) as connection:
    print(connection.execute('''
        SELECT event_type, host_name, count(*) AS records
        FROM events GROUP BY event_type, host_name
        ORDER BY event_type, host_name
    ''').fetchall())
    print(connection.execute('''
        SELECT count(*) AS batches, sum(source_rows), sum(inserted_rows)
        FROM imported_batches
    ''').fetchall())
PY
```

Run the saved, bounded investigation queries after the import closes:

```sh
"$datastore_env/bin/python" - "$datastore_data/events.duckdb" <<'PY'
import pathlib
import sys
import duckdb
with duckdb.connect(sys.argv[1], read_only=True) as connection:
    statements = connection.extract_statements(
        pathlib.Path('queries/security-events.sql').read_text())
    for number, statement in enumerate(statements, 1):
        print('Query', number)
        print(connection.execute(statement).fetchall())
PY
```

The queries use the capture's latest event as their time boundary and show up to
100 recent execution/file/network rows. Query results can contain process
arguments and paths; keep them local.

## Verification and limits

Ten offline tests cover timezone and nanosecond-source timestamp/JSON preservation, missing optional
context, same-batch retries, within/across-batch duplicates, malformed/empty
inputs, unsupported schema versions, post-publication recovery, transactional
rollback, and corrupt published files. Fixtures are synthetic and contain no
production captures. The timestamp test also fetches timezone-aware Python
results, covering the required `pytz` dependency.

**Live Docker Desktop evidence, 2026-10-27:**

- Pinned Vector validation and all 11 transform tests passed. The existing
  host/container execution and exit acceptance check passed with exactly
  `host` and `event_type` metric labels.
- Both policies loaded. A fresh probe's 11 records (2 process, 8 file, 1 TCP)
  retained their exact raw objects and normalized details across staging,
  Parquet, and reopened native DuckDB. Negative path/binary checks passed.
- One completed batch contained 591 records; all 591 were inserted. An
  identical retry reported `already_imported` and inserted zero rows.
- The JSONL was 1,515,119 bytes, Zstandard Parquet 66,933 bytes, and native DB
  3,944,448 bytes. Conversion/import took about 115 ms; a query for the probe
  execution ID took about 1 ms. These tiny, warm local checks are not a scale
  benchmark or evidence of daily growth.
- All four saved queries executed successfully. The database was closed and
  reopened read-only for validation.
- An isolated Vector container recovered unread records from a retained
  rotated file and a new active file after stop/restart with persistent
  checkpoints. It did not replay the checkpointed record. A separate 65-second
  idle interval retained the same counter value. No collector log was renamed
  or truncated for this test.
- Main-stack memory at one sample was about 97 MiB Tetragon, 44 MiB Vector,
  and 118 MiB Juice Shop, within their existing limits. No limits were raised.

The local evidence capture is outside Git in a temporary directory. It is test
data, not a durable deployment or scheduled ingestion service.

The importer detects ordinary input changes during reading, but cannot establish
that a writer is stopped; completed inputs are a caller requirement. Atomic
publication and transactions are not tested power-loss durability guarantees.
The command does not watch directories, schedule imports, expire data, enforce
a disk quota, or provide an API. JSONL, Parquet, and native tables consume
separate space; retention and operational recovery remain milestone gates.
Keep all three representations outside Git and treat their process arguments
and host context as sensitive.
