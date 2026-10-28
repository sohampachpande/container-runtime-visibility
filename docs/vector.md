# Vector event pipeline

## Scope and status

Milestone 4 introduces a pipeline for the already verified Tetragon process
execution and exit events. On 2026-10-25, Vector 0.60.0 was deployed on the VM
from `m4/pipeline`; configuration validation, four transform tests, live event
correlation, and counter-label checks passed. Raw cgroup enrichment is still
deferred. Prometheus scraping and execution of a rate query remain unverified.

```text
/var/log/tetragon/tetragon.log
  → file source with persistent read checkpoints
  → common envelope for process/file/network events
      → structured JSON on stdout
      → persistent JSONL staging
      → runtime_events_total{host,event_type} on /metrics
  → rejected records on stderr
```

The pinned image is `timberio/vector:0.60.0-alpine`. See the upstream
[Docker installation guide](https://vector.dev/docs/setup/installation/platforms/docker/).
No new APT package, Docker socket access, or privileged container is needed.

## Event and metric choices

The common log envelope contains `schema_version`, `collector`, `timestamp`,
`event_type`, `category`, `host`, `process`, `parent`, and `container`, plus
nullable `policy`, `hook`, `file`, and `network` details. The complete original
JSON object is retained under `tetragon`, including exit status and event-specific
fields. `container.id_prefix` carries Tetragon's truncated Docker identity;
it is not presented as a full container ID. Missing metadata remains null.

Milestone 5 adds explicit policy-and-hook mappings for `runtime-sensitive-file`
and `runtime-network-connect`. Both use transport type `process_kprobe`, so
the transport alone does not determine the category. File details contain path,
permission mask, and permission-hook result; network details contain endpoints,
family, protocol, state, outbound direction, and `connect_attempt` observation.
Arguments are selected by their exported type, not a fixed array position.
Absent optional context remains null; unknown valid payloads use category
`other` and retain their original object. Malformed JSON, missing timestamp or
host, non-object payloads, and ambiguous multiple payloads are rejected.

The envelope remains schema version 1 with additive fields. The importer assigns
`event_id` from canonical original JSON during projection into Parquet/DuckDB;
Vector does not assign a second identity. Previously staged version-1 records
may lack the added category/details, and imports do not rewrite existing rows.
Eleven pinned-image tests cover the original lifecycle behavior, both new
mappings, optional fields, unknown payloads, rejection, and label isolation.

The counter increases once per normalized record and has exactly two labels:
host and event type. The `event_type` label uses `metric_event_type`, a bounded
list of pinned Tetragon payload types; future unrecognized types use `other`.
PIDs, executable paths, arguments, execution IDs, and
container IDs remain in logs. The counter counts exported records, including
startup-discovered processes; it is not a count of all kernel calls.

A counter supports event-rate queries such as
`rate(runtime_events_total[1m])` when scraped by Prometheus. The exporter alone
does not calculate that query or retain a time series; deploying Prometheus
is outside this step. Exported counters omit event timestamps so Prometheus
can assign scrape time. Counter expiration is now disabled explicitly, so idle
series retain their accumulated value while Vector runs. Counters reset when
Vector restarts. An isolated pinned-image check retained the same counter value
after 65 seconds without input on 2026-10-27.

## Permissions, checkpoints, and resource limits

Tetragon retains its root-only log permissions. Vector runs as UID 0 inside
its container to read those files, with all Linux capabilities dropped and
the source log directory mounted read-only. This is a lab permission choice;
a dedicated user with narrowly scoped file access can be evaluated later.

The deployment uses one Vector worker, a provisional 128 MiB container limit,
and loopback port 9598. Its JSON console output is retained in Docker logs
with three 10 MB files. This is bounded demo retention, not durable archival.
Measure actual memory alongside Tetragon and Juice Shop before adding services.

The file source now includes the active file and Tetragon's timestamped rotated
`.log` backups, reading uncheckpointed files from the beginning. This trades
initial historical replay for an opportunity to recover retained events after
an outage and rotation. A named volume preserves checkpoints on subsequent
runs. The directory mount allows rotation to be followed. Events deleted by
Tetragon's retention cannot be recovered, and checkpoints are not an exactly-
once guarantee. An isolated pinned-image test stopped Vector, rotated its
controlled input, appended a record to the retained backup, created a new active
file, and restarted with the same checkpoint directory. Both unread records
were recovered and the checkpointed record was not replayed. This verifies the
file-reader configuration, not collector-driven rotation under sustained load
or crash/power-loss recovery.

## Validate and run with Compose

The root `compose.yaml` contains Vector alongside Tetragon and Juice Shop.
For development, combine it with `compose.desktop.yaml`. Follow
[the Docker Desktop guide](docker-desktop.md) for configuration validation,
the Vector tests, startup, logs, and shutdown commands.

The override creates the `runtime-visibility-desktop-vector-data` checkpoint
volume automatically. Vector reads the shared Tetragon log volume read-only.
No separate Vector Compose file or hand-written `docker run` deployment is needed.

## Verify the pipeline

The [Desktop acceptance check](docker-desktop.md#repeat-the-live-acceptance-check)
generates fresh host and Juice Shop executions. Match their host PIDs,
executables, execution IDs, and original event objects between the raw file
and normalized output. Confirm that both event counters advance and that
labels remain limited to `host` and `event_type`.

Use the [Desktop lifecycle commands](docker-desktop.md#inspect-output-and-stop)
to inspect resource use and stop the lab. Compose teardown without `-v`
retains the named event and checkpoint volumes.

The full Compose deployment is verified on Docker Desktop. Run
`python3 scripts/verify-pipeline.py --desktop` for the repeatable acceptance
check. It validates event correlation and counter labels; it does not execute
a Prometheus rate query or test checkpoint recovery.

## Milestone 5 staging

The `staging` sink writes the normalized process/file/network envelope
as JSONL under `/var/lib/vector/staging/` on the persistent checkpoint volume.
Pinned-image validation, all 11 transform tests, execution/exit acceptance, and
fresh all-category staging/Parquet/DuckDB checks passed on 2026-10-27.

Use [the batch ingestion guide](datastore-ingestion.md) to stop/flush Vector,
copy completed files, and convert/import them into Parquet and DuckDB. Date-based
filenames can be reopened by late events, so do not convert active files.
Staging retention is not automatically bounded; measure usage and implement
cleanup before relying on unattended capture.
