# Milestones

## Milestone 1: Docker OOM debugging demo — complete

The demo Python service runs in a container with a 64 MiB memory limit. A 1 GiB allocation request causes the Linux kernel to kill the process.

**Verified evidence**

- Application logs show allocation progress until termination.
- Docker reports OOMKilled=true and exit code 137.
- Docker reports an applied memory limit of 64 MiB.

See [the debugging workflow](debugging-workflow.md).

## Milestone 2: Linux test environments

The project was cloned to a Raspberry Pi 5, where Docker Compose reproduced and
diagnosed the memory-constrained OOM kill. See [the Raspberry Pi setup](linux-host-setup.md).

**Raspberry Pi status**

1. Docker Compose starts the demo service.
2. The health endpoint responds successfully.
3. The 1 GiB allocation request reproduces the OOM kill.
4. The cgroup v2 memory controller is enabled with cgroup_enable=memory.
5. The running Raspberry Pi kernel does not expose BTF:
   `CONFIG_DEBUG_INFO_BTF` is unset and `/sys/kernel/btf/vmlinux` is absent.

The Pi remains useful for the Docker OOM demo but is not the current Tetragon
test host.

**GCP collector test host — verified**

1. Terraform provisions an Ubuntu 24.04 `e2-micro` VM in `us-west1-b`.
2. The running kernel exposes `/sys/kernel/btf/vmlinux` and enables
   `CONFIG_DEBUG_INFO_BTF=y` and `CONFIG_DEBUG_INFO_BTF_MODULES=y`.
3. The host uses cgroup v2 with the memory controller enabled.
4. IAP SSH and passwordless sudo work.

See [the Terraform lab setup](../archive/terraform/README.md).

**Preflight result**

The BTF, cgroup, process-exec tracepoint, and OOM tracepoint checks pass. The
installed GCP kernel tools do not include a usable `bpftool` binary, so its
feature probe is unavailable on this Ubuntu image. This is not a blocker for
Milestone 3: Tetragon startup is the functional check that its CO-RE programs
can load on this host.

## Milestone 3: eBPF event pipeline and process execution

Deploy Tetragon as the host-level collector described in [the eBPF collector design](ebpf-collector.md). Its first event source is process execution.

**Success criteria**

1. Tetragon starts successfully on the GCP Linux host.
2. A process execution produces one Tetragon JSON event with host PID, process name, and cgroup identity.
3. The event is written to the collector's JSON output stream.

**Implementation status — partially verified on 2026-10-25**

The [Tetragon guide](tetragon.md) and
[`collector/tetragon/tetragon.conf.d/`](../collector/tetragon/tetragon.conf.d/)
provide pinned package installation, local JSON export with log rotation, and
a live host-process probe.

1. Installed Tetragon and tetra `v1.7.0` from the upstream amd64 package after
   verifying its SHA-256 checksum. Bundled `bpftool v7.3.0` is usable at
   `/usr/local/lib/tetragon/bpftool`; the Ubuntu wrapper remains unusable.
2. The `__base__` BPF sensor loaded on kernel `7.0.0-1014-gcp`. The service is
   enabled on boot and running with zero automatic restarts. A deliberate
   restart applied the loopback health endpoint configuration.
3. After that restart, measured service memory was about 57 MiB, with a 132 MiB
   peak; host available memory was about 449 MiB. These are point-in-time
   observations, not a capacity guarantee for the demo and Vector together.
4. A fresh `/usr/bin/sleep 60` execution at `2026-10-25T17:48:56Z` produced a
   `process_exec` JSON record with PID `4268` and flags `execve clone`. The PID
   and executable matched the live process under `/proc`. The earlier startup
   probe was flagged `procFS` and was excluded as proof of new execution.
5. The JSON export is root-readable at `/var/log/tetragon/tetragon.log`. The
   matching raw probe record is retained only on the VM at
   `/var/log/tetragon/m3-probe.json`. gRPC uses a Unix socket and the health
   endpoint listens on `127.0.0.1:6789`.

**Remaining gap**

The captured execution record has no cgroup field. Its host cgroup membership
was independently checked through `/proc/4268/cgroup` while the process was
alive, but a `/proc` lookup alone does not satisfy criterion 2. Choose an
enrichment approach from the actual schema before completing this milestone.
The Vector pipeline is tracked under Milestone 4. The Docker probe below verifies native container
identity, but still does not export a raw cgroup field.

**Docker attribution probe — verified on 2026-10-25**

1. Built the Python Dockerfile (now `test-app/Dockerfile`, originally
   `demo-app/Dockerfile`) as `runtime-visibility-demo-app:m3`
   and started `demo-app-m3` with a 64 MiB limit and port 8080 bound to loopback.
   Docker Compose is unavailable on this VM, so the test used Docker directly.
   The health endpoint returned `ok`; no OOM request was made.
2. The application startup event at `2026-10-25T17:58:59Z` identified host PID
   `4707`, `/usr/local/bin/python`, arguments `app.py`, and `in_init_tree: true`.
3. A `docker exec` probe at `2026-10-25T17:59:28Z` identified host PID `4851`,
   `/bin/sleep`, arguments `120`, and `in_init_tree: false`.
4. Both events included `process.docker: c1813a7ce3b4031489d42bb553af5ad`, a
   prefix matching Docker's full container ID. Docker confirmed the host PIDs;
   `/proc/<pid>/cgroup` confirmed both processes belonged to that container's
   `docker-<full-container-id>.scope` cgroup.
5. Native container identity therefore works without custom enrichment. Neither
   event contains a raw cgroup ID/path or a human-readable container name.
   The matching raw records remain only on the VM at
   `/var/log/tetragon/m3-container-probe.json`.

**Juice Shop workload switch — verified on 2026-10-25**

1. The default `compose.yaml` now selects `bkimminich/juice-shop:v20.3.0`,
   loopback port 3000, and a 384 MiB memory limit. The Python exercise remains
   available separately through `compose.oom.yaml` with its 64 MiB limit.
2. On the VM, stopped `demo-app-m3` and started `juice-shop-m3` using equivalent
   Docker CLI settings because Compose is not installed. HTTP `/` responded
   successfully, `OOMKilled=false`, and measured container memory was about
   136 MiB. Host available memory was about 245 MiB; no VM resize was made.
3. The raw Tetragon log contains a startup event at `2026-10-25T18:23:53Z` for
   host PID `5638`, `/nodejs/bin/node`, arguments `/juice-shop/build/app.js`,
   effective UID `65532`, and `in_init_tree: true`. Its `docker` prefix
   `2118c7fcf4cda7e96eb5baba0ebad80` matched Docker's container ID; the live
   `/proc/5638/cgroup` path independently confirmed membership.
4. Tetragon remained active with zero automatic restarts. The raw cgroup
   export gap remains; no custom tracing policies or Vector pipeline were
   added by this workload switch.

## Milestone 4: Vector event pipeline

Route Tetragon events through Vector and derive low-cardinality event-rate metrics.

**Success criteria**

1. Vector receives structured events from Tetragon.
2. Vector outputs normalized events and an event-rate metric.
3. Metric labels exclude high-cardinality fields such as PID and raw file paths.

**Implementation status — initial pipeline verified on 2026-10-25**

[`collector/vector/`](../collector/vector/) contains the file source,
execution/exit normalization, bounded console output, an event counter, and
inline transform tests. [The Vector guide](vector.md) describes deployment and
live acceptance checks.

1. Deployed `timberio/vector:0.60.0-alpine` as `vector-m4`, with a 128 MiB
   limit, one worker, a read-only Tetragon log-directory mount, persistent read
   checkpoints, and loopback port 9598. No new APT packages were needed.
2. Vector configuration validation and all four transform tests passed.
   The tests cover process execution, exit status preservation, malformed JSON
   rejection, and metric-label isolation.
3. Fresh host execution and exit records for PID `7362` (`/usr/bin/sleep`),
   plus a Juice Shop Node.js execution for PID `7383`, were matched between
   Tetragon's raw file and Vector's normalized output. The complete `tetragon`
   object equaled the original event in each case. Container identity matched
   Docker's ID prefix and the live host cgroup.
4. The execution counter increased by 9 and the exit counter by 10 during the
   probe window, which included host background activity and verification
   commands. Labels were exactly `event_type` and `host`, excluding PIDs,
   paths, arguments, and container IDs.
5. Measured Vector container memory was about 13 MiB within its 128 MiB limit;
   Juice Shop used about 112 MiB and the host had about 248 MiB available.
   Both containers were healthy, and Tetragon had zero automatic restarts.

The exported metric is `runtime_events_total`, a counter suitable for deriving
event rates. A Prometheus scrape and `rate(runtime_events_total[1m])` query have
not yet been validated; no standalone per-second rate series is emitted.
Read-checkpoint recovery and log-rotation behavior also need operational
testing before relying on delivery guarantees. Raw cgroup enrichment remains
deferred from Milestone 3.

**Root Compose packaging — verified on 2026-10-25**

The root `compose.yaml` declares the three separate Tetragon, Vector, and
Juice Shop containers, replacing the standalone Vector Compose definition.
It retains the host log directory and external `vector-m4-data` checkpoints,
and adds privileged host access for the pinned Tetragon image. The
[Compose guide](../archive/docs/compose.md) documents the finalized setup and commands.
Installed Ubuntu's `docker-compose-v2` version
`2.40.3+ds1-0ubuntu1~24.04.1`, validated the root file, and passed all four
Vector tests through Compose. The three pinned containers are running, and
Tetragon's base sensor loaded successfully with exactly one collector process.

The repeatable `scripts/verify-pipeline.py` check matched fresh host execution
and exit events for PID `11020` and a Juice Shop execution for PID `11040`,
including exact raw-event preservation, host name, Docker identity prefix,
and live cgroup membership. Counter increments were verified with only
`host` and `event_type` labels. A subsequent root `docker compose up --detach`
was repeatable without recreating services.

The services had no OOM kills or automatic restarts, and Juice Shop HTTP
readiness passed. Measured memory was about 65 MiB for Tetragon, 23 MiB for
Vector, and 108 MiB for Juice Shop, leaving about 275 MiB available on the VM.
The existing external checkpoint volume was retained. Full restart/checkpoint
recovery, rotation behavior, and a Prometheus rate query remain unverified.

**Docker Desktop local test — verified on 2026-10-25**

`compose.desktop.yaml` adapts the root stack to the local ARM64 Docker Desktop
VM (`7.0.14-linuxkit`, cgroup v2, readable BTF, about 4 GiB RAM). The privileged
collector loaded its base sensor. The override shares raw events through a
named volume and isolates Desktop checkpoints from the GCP volume.

All four Vector tests passed. The repository acceptance check with `--desktop`
matched Linux VM execution/exit PID `1246` and Juice Shop execution PID `1260`
through raw and normalized events. It verified node `docker-desktop`, the
LinuxKit kernel, live container cgroup membership, and counter increments with
exactly the `host` and `event_type` labels. The VM probe uses a temporary
namespace-entering helper and retains that helper's container cgroup; no macOS
process was used as the observed host probe.

Juice Shop HTTP readiness passed; all containers were running with no OOM
kills or automatic restarts. Measured memory was about 75 MiB for Tetragon,
41 MiB for Vector, and 102 MiB for Juice Shop. See
[the local Desktop guide](docker-desktop.md) for reproducible commands.

## Milestone 5: Security event coverage and searchable storage

Collect process execution, sensitive-file access, and network connections through
one shared Vector pipeline, then retain the events in a queryable datastore.
OOM collection is removed from this milestone; the original OOM exercise remains
available separately. See [the implementation plan](../plans/milestone-5-datastore-plan.md).
The [datastore decision](datastore.md) selects local JSONL staging, Parquet batch
conversion, and ingestion into native DuckDB tables. ClickHouse is the preferred
next step if continuous ingestion or shared query serving is needed.

**Core path verified on Docker Desktop, 2026-10-27; milestone in progress**

1. Tetragon enabled both observation-only policies. A fresh Node.js probe
   produced execution/exit, file-permission, and TCP-attempt records sharing an
   execution ID and matching Juice Shop's Docker identity. Protected/control
   paths and a non-Node negative probe behaved as configured.
2. Vector's common envelope maps the two policy/hook combinations into file
   and network categories, preserves original JSON, and retains optional null
   context. Pinned-image validation and 11 transform tests passed; the existing
   process acceptance check passed with bounded metric labels.
3. All 11 fresh probe records (2 process, 8 file, 1 network) survived staging,
   Parquet conversion, and native DuckDB with exact original-object equality.
   The completed batch imported 591 records; retry inserted zero duplicates.
   Reopened read-only queries and all four saved SQL investigations passed.
4. Ten offline ingestion tests cover types, raw preservation, optional fields,
   duplicates, malformed inputs, post-publication retry, and transactional
   rollback. The Python dependency list includes `pytz` for timezone-aware
   query results.
5. An isolated pinned Vector test recovered unread records from a retained
   rotated backup and new active file after restart without replaying the
   checkpointed record. Counters retained their value after 65 idle seconds.
6. Measured memory was about 97 MiB Tetragon, 44 MiB Vector, and 118 MiB Juice
   Shop, within existing limits. The 591-record batch took about 115 ms to
   convert/import; one execution-ID query took about 1 ms. These are small
   local measurements, not sustained throughput or capacity evidence.

The milestone remains open for retention/disk-budget cleanup and sustained-load/
crash-recovery acceptance. Collector-driven rotation and full-disk behavior
remain unverified; no automatic ingestion scheduler or expiry exists. The file
scope is the Node.js protected probe prefix, and networking covers Node.js TCP
attempts. IPv6, refused connections, broader executable coverage, and raw cgroup
export remain unverified. See [the raw-event guide](security-events.md) and
[storage evidence and limits](datastore-ingestion.md#verification-and-limits).

**Success criteria**

1. Process execution events identify the current Juice Shop demo workload.
2. Sensitive-file observations include the accessing process, target path,
   operation semantics, and available workload identity.
3. Network observations initially cover outbound TCP connection attempts and
   include process, destination IP/port, and available workload identity;
   distinguish attempts from successful connections.
4. Vector handles all three categories through shared normalization while
   preserving the original event and event-specific payload.
5. The output includes cgroup identity and container identity when available,
   with missing metadata explicitly documented.
6. Fresh events from all three categories pass through local JSONL staging,
   Parquet conversion, and native DuckDB tables, queryable by time, workload,
   and category. Verify persistence, bounded outage recovery, duplicate-safe
   import, rollback, retention, and local resource use.

**Future security coverage**

Additional coverage is chosen after the three categories are validated.
Candidates include inbound connections, UDP activity, and privilege-related
events. Process exit remains part of the existing process lifecycle pipeline.
UI, alert rules, and a custom API follow the initial DuckDB SQL query checks.

An optional [local query dashboard](datastore-ui.md) was added alongside
Milestone 5 on 2026-10-28 at the user's request. It runs DuckDB and Streamlit in
its own container, reads Vector staging read-only, and imports complete-line
snapshots on demand into a separate persistent volume. It supplies category/
workload/time filters, charts, event inspection, and a bounded read-only SQL
editor. The original host batch workflow remains available. This does not
complete Milestone 5's outstanding retention and operational acceptance work.

## Milestone 6: Agent packaging

Package the agent as a dedicated Docker image and prepare a Kubernetes DaemonSet manifest.

**Success criteria**

1. The agent image observes host events without being embedded in application images.
2. The DaemonSet schedules one agent per eligible Kubernetes node.
3. The manifest documents its host mounts and required capabilities.

## Future work

### Platform-backed anomaly detection

Evaluate platform-backed anomaly scoring when the local analytical workflow
needs a shared service. Follow [the datastore decision](datastore.md); no
platform-backed anomaly engine has been selected or validated.

**Success criteria**

1. The platform receives normalized runtime-security events.
2. An anomaly detector scores at least one low-cardinality event-rate feature.
3. The detector result can be correlated with the underlying security events.

### Application and kernel telemetry correlation

Correlate application logs, traces, and metrics with kernel events. Vector normalizes and enriches the event stream; a later platform retains the enriched data and supports cross-event investigation.

**Success criteria**

1. Application telemetry and kernel events share workload identity through container or cgroup metadata.
2. Application logs include trace context and an application or agent run identifier.
3. A bounded correlation maps an application or agent run to kernel events from the same workload and time window.

### AI agent runtime monitoring

Correlate agent runtime telemetry with kernel events to detect unexpected data and network access by an AI agent.

**Success criteria**

1. Agent runtime telemetry identifies the agent, run, tool, and workload without collecting raw prompt or tool payloads by default.
2. Kernel events can be correlated to the agent workload and a bounded time window.
3. The system detects at least one unexpected outbound destination or file-access pattern.
