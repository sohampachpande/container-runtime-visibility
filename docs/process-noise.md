# Workload-scoped process telemetry

Process execution/exit records were dominated by the lab itself. One captured
hour contained 720 dashboard records and 582 Linux VM/runtime records, with
none from Juice Shop. The dashboard's Docker health check launches shell/Python
processes every 30 seconds, with additional runtime activity around each check.

The pipeline now retains process lifecycle events only for the selected Juice
Shop container. This is a workload boundary, not a binary allowlist: shells,
Python, tools launched by Node, and `docker exec` processes remain eligible when
their exported Docker identity matches that container.

```text
Tetragon raw JSON: unchanged host-wide collection and bounded log retention
  → Vector normalization and workload-identity match
      → process_exec / process_exit: selected workload only
      → file / network / other valid events: existing routing unchanged
          → normalized console, JSONL staging, and bounded-label metrics
              → snapshot import → Parquet / DuckDB → grouped dashboard
```

This reduces new normalized/staged process records and downstream storage.
It does not reduce Tetragon's kernel work or raw-log volume. Excluded process
records are ordinary filtered observations, not malformed/rejected records.
Their raw records remain available until Tetragon's existing retention removes
them. File/network collection still has the Node.js/protected-prefix limits
described in [the policy guide](security-events.md).

## Select the current container

Start Tetragon and Juice Shop first, then resolve the running workload's full
Docker ID through the host-side setup script. Neither Vector nor the dashboard
gets access to the Docker socket.

```sh
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml up --detach tetragon juice-shop
python3 scripts/select-workload.py --desktop
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml --profile ui \
  up --detach --build vector datastore-ui
```

For native Linux, omit `--desktop` and the Desktop Compose flags. Leave out the
`ui` profile and `datastore-ui` service if the dashboard is not needed.

The script atomically writes `MONITORED_CONTAINER_ID` to
`/tmp/runtime-visibility-workload.env` with owner-only permissions. Both services
read it via Compose `env_file`. Set `WORKLOAD_ENV_FILE` consistently for the
script and Compose if a different location outside Git is needed. The exported
Tetragon prefix must be at least 12 characters and match the beginning of the
configured full ID; a PID is never substituted for missing container identity.

**Refresh the selection after recreating Juice Shop**, and after losing the
temporary environment file. Then recreate Vector and the UI so the changed
environment is applied. Restarting an existing container alone does not reload
its environment. A stale selection can miss the replacement workload; a missing
or invalid selection excludes all lifecycle events from staging. The UI shows
a configuration warning when its selection is missing/invalid. The acceptance
script checks the live container ID against Vector's configuration before
generating probes. There is no automatic Docker discovery/watch service.

## Known assumption: manually maintained container identity

The current lab assumes one selected Juice Shop container and an operator who
refreshes its ID whenever the container is recreated. A container ID identifies
one container instance; it is not a stable workload identity. Replicas and
replacement containers are not discovered automatically.

**Failure mode:** the saved ID can remain syntactically valid after replacement.
Vector then excludes the replacement's execution/exit records from normalized
output, staging, and metrics. The dashboard's missing/invalid-ID warning does
not detect this stale-but-valid selection, so old data can still appear under
the monitored-workload label while new process telemetry is absent. Raw
Tetragon collection continues, subject to its existing log retention.

The acceptance script detects a mismatch when explicitly run, but it is not a
continuous freshness check. This manual setup is a lab limitation, not a
production workload-selection design.

**Required follow-up:** select a stable workload identity, such as Compose
project/service labels, and automatically resolve and refresh its current
container IDs. Support replacement and multiple matching instances. Reliable
Docker metadata discovery/enrichment and its access permissions still need a
design; neither Vector nor the UI currently has Docker socket access.

Make missing or stale discovery visibly unhealthy, and verify recreation and
replica coverage with fresh execution/exit probes before claiming automatic
workload monitoring. This is pending work, not implemented behavior. The
Node-only file/network policies remain a separate coverage limitation.

## Dashboard behavior

- Explore defaults to **Juice Shop (monitored)**, with the selected ID prefix
  visible. **All workloads** and individual historical prefixes remain available.
  SQL always queries the complete imported database independently of Explore.
- Time windows use the latest imported event for the selected workload, keeping
  an old capture usable even when unrelated infrastructure has newer events.
- Process rows are grouped by **host plus execution ID**. Execution and exit
  records become one row with executable, parent, arguments, start/exit time,
  observed duration, exit status/signal, and links to the original records.
  Reused PIDs and different hosts do not get merged. Missing execution IDs stay
  separate using event IDs.
- **No exit observed** is an observation, not proof that the process is running.
  Exit-only rows have `execution_seen = false`. Duration requires usable start
  and exit timestamps; missing status stays null instead of inventing success.
- Lifecycle time filtering uses the last observed record. Charts and counters
  still count exported records, so an execution and exit can count twice while
  appearing in one table row. File/network details stay in their own table.

The `process_lifecycles` SQL view exposes the same grouping. For example:

```sql
SELECT executable, parent_executable, started_at, exited_at,
       duration_seconds, exit_status, exit_signal, observation
FROM process_lifecycles
ORDER BY event_time DESC
LIMIT 100;
```

Existing JSONL, snapshots, Parquet, and database rows are preserved. Old
infrastructure records are still accessible through All workloads or SQL.
Reimporting a historical staging file can still copy those old records into
an overlapping snapshot/Parquet batch; native event-ID deduplication remains
in effect. This change is not retention cleanup or a historical purge.

## Validation

The pinned Vector configuration and 16 transform tests pass. The container test
suite passes all 26 tests, including lifecycle pairing, PID reuse, cross-host and
missing identities, exit-only records, duration/status semantics, workload UI
defaults, and the existing ingestion/query tests. Live validation confirmed
selected-workload execution/exit retention, raw-only host events, unchanged
file/network delivery, and the grouped UI. The dashboard imported another
21,858 records, bringing the database to 50,418 records, and displayed the fresh
probe executions paired with their exits. See the current milestone evidence.

The first wide grouping query exhausted the 128 MB query budget on the existing
7,969-record capture. The final query groups typed identities/event IDs first,
then limits the dashboard to 200 identities before loading JSON details. Both
selected- and all-workload tables pass against a private copy of that capture
under the same limit, and a synthetic 8,000-identity/long-argument regression
test passes. Container and engine limits were not raised. SQL queries remain
subject to the normal memory/time limits; narrow large investigations by
workload/time and project the fields needed.

Live UI import also exposed existing wide-row validation/insertion OOMs on
55.9 MiB and 66 MiB historical batches. Narrow row-position deduplication avoids
sorting wide payloads before native insertion. Those real 18,443- and
21,786-record batches pass in isolation at the existing 256 MB limit.
The user selected bounded validation instead of a larger memory budget.
Validation now checks row counts and compares complete typed-row SHA-256
fingerprints in ranges of at most 512 rows, including positions and duplicate
occurrences. The synthetic 16,000-row/long-argument regression and retry pass;
tests also reject metadata changes beyond the first range, reordered rows, and
extra/missing rows. No history was discarded and no memory limits were raised.
Row width and the remaining conversion/import operations can still exhaust the
budget; see [ingestion limits](datastore-ingestion.md#verification-and-limits).
