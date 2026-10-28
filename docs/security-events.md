# Milestone 5: raw security-event probes

Status: both policies loaded and controlled probes passed on Docker Desktop
on 2026-10-27 (event timestamps are 2026-10-28 UTC).
This is step 1 of [the action plan](../plans/milestone-5-datastore-plan.md).

## Verified local evidence

- Tetragon v1.7.0 enabled both policies on ARM64 `7.0.14-linuxkit` in
  observation mode. The existing execution/exit acceptance check also passed.
- One fresh Node.js execution produced 2 process lifecycle records, 8 file
  permission records, and 1 TCP-connect record. All shared one execution ID.
  Docker ID prefixes matched Juice Shop; its main process's live cgroup
  independently confirmed container membership. No raw cgroup field was added.
- File arguments contain `file_arg.path` and a separate `int_arg` mask;
  the return is `return.int_arg`. Masks 2 and 4 appeared for writes and reads,
  with result 0. Directory checks and repeated-looking checks also appeared.
- The socket argument contains family, protocol, source/destination address
  and port, and state. The IPv4 probe reported `TCP_SYN_SENT`; application
  success was checked separately, not inferred from that state.
- The Node.js control path and a non-Node shell/`cat` access to a protected
  prefix were absent from file-policy output.
- All 11 fresh records reached normalized staging, Parquet, and reopened
  DuckDB with exact original-object preservation. See
  [the storage evidence](datastore-ingestion.md#verification-and-limits).

## Observations and limits

| Category | Collector source | Initial scope |
|---|---|---|
| Process execution/exit | Existing base sensor | Existing host and container observations |
| Sensitive-file access | `security_file_permission` kprobe and return probe | Node.js and disposable protected-path prefix |
| Outbound TCP attempt | `tcp_connect` kprobe | `/nodejs/bin/node` executions |

The policies use `Post` only. No enforcement, payload bytes, or real credentials
are collected. The file policy reports a permission mask and permission-hook
result, not bytes read. The network hook observes a TCP connection attempt,
not proof of establishment. The probe independently confirms its successful
connection at application level. IPv6 and refused connections remain untested.

The file prefix `/tmp/runtime-visibility-sensitive-` represents a protected
resource for the controlled exercise. This is not general sensitive-path
coverage. Neither policy's binary selector isolates a specific container:
another workload using `/nodejs/bin/node` can match. Verify Docker attribution
in each record. Paths, host PIDs, and workload fields must be inspected in actual
output before extending normalization.

The policies follow pinned upstream examples for
[file permissions](https://github.com/cilium/tetragon/blob/v1.7.0/examples/tracingpolicy/filename_monitoring.yaml)
and [TCP hooks](https://github.com/cilium/tetragon/blob/v1.7.0/examples/tracingpolicy/tcp-connect.yaml).
Kernel compatibility is established by successful loading and events, not YAML
syntax. A missing hook or failed sensor is a failed acceptance check.

## Why the MVP filters for Node.js

Tetragon observes Linux kernel operations independently of application language.
Our `matchBinaries` selectors deliberately retain file/network observations
only when the current executable matches `/nodejs/bin/node`. This is the
executable in the Juice Shop image, not a filter for all JavaScript programs
or all processes inside that container. It keeps the first exercise focused.

| Process performing the operation | File policy | Network policy |
|---|---|---|
| `/nodejs/bin/node` | Matches only the protected probe-path prefix | Matches TCP attempts reaching `tcp_connect` |
| A Node.js executable at another path | Excluded | Excluded |
| Python interpreter or a Python-based agent | Excluded | Excluded |
| A different executable launched by Node.js, such as `curl` | Excluded | Excluded |

The live negative probe verifies exclusion of shell/`cat`; the other executable
examples describe the configured selector, not separately tested interpreters.
The base sensor still collects execution/exit independently of these selectors,
so an excluded Python process can have execution telemetry without file/network
telemetry. Executing native library code inside a matching Node.js process does
not by itself change the executable identity. Launching another executable does.

To extend coverage later:

1. Inspect actual Tetragon execution events to discover interpreter/executable
   paths. Add the desired paths to `matchBinaries.values` in both policies.
   Do not assume a shell command name or symlink spelling is the exported path.
2. Alternatively remove the binary filter and retain suitable resource/workload
   filters. Removing it from the file policy still leaves the protected-path
   filter; removing it from the network policy broadens collection across the
   Linux node. Measure event volume and resource use before broadening scope.
3. Validate workload/descendant-aware selection where supported by the pinned
   version and deployment. Interpreter filtering alone cannot distinguish two
   agents using the same interpreter, and application children may execute
   different binaries. Verify exported workload identity and define the intended
   process-tree coverage explicitly.

An agent framework does not require a new kernel collector. Its runtime and
tool subprocesses must fall within the chosen collection scope. Collection
scope decides which observations exist; later detection rules decide which
observations are suspicious. See [Tetragon selectors](https://tetragon.io/docs/concepts/tracing-policy/selectors/).

## 1. Apply the collector configuration

Run from the repository root on the Mac. First validate the merged Compose
configuration; then start/update Tetragon and Juice Shop. The collector will
be recreated to apply the new policy mount and configuration. This causes an
observation gap; it does not remove the existing event/checkpoint volumes.

```sh
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml config --quiet
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml up --detach tetragon juice-shop
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml logs --tail 150 tetragon
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml exec -T tetragon tetra \
  --server-address unix:///var/run/tetragon/tetragon.sock tracingpolicy list
```

Require the base sensor and both `runtime-sensitive-file` and
`runtime-network-connect` policies to load and report enabled status without
errors before proceeding. Check that only one collector is running. Native
Linux uses the root Compose file without the Desktop flags; policy mounts and
the directory setting are present in both configurations.

## 2. Capture raw events in one terminal

Create a fresh local capture outside the repository, print its path, and follow
the raw log across rotation. This records all exported activity during the
window, so keep the capture local and treat it as sensitive.

```sh
m5_capture=$(mktemp /tmp/runtime-visibility-m5.XXXXXX)
printf 'Raw capture: %s\n' "$m5_capture"
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml exec -T tetragon \
  tail -n 0 -F /var/log/tetragon/tetragon.log > "$m5_capture"
```

Wait for the follower to attach. Leave it running while generating the probe
in another terminal. Press Ctrl-C after events arrive. This follower avoids
reapplying an obsolete byte offset; it is not a durable delivery guarantee.

## 3. Generate controlled activity in another terminal

Execute the repository script through Juice Shop's existing Node.js executable.
No shell, additional workload image, or package installation is required in
the application container. The Node process performs protected and control
file accesses and a TCP connection to its own IPv4 loopback listener, prints
the expected paths and endpoint, and removes its temporary directories.

```sh
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml exec -T juice-shop \
  /nodejs/bin/node < scripts/probe-security-events.js
```

The printed PID is a container PID. Match the raw `process_exec` to the Node
execution and use its execution identity to correlate file/network records;
do not compare the container PID directly to Tetragon's host PID.

Expect a protected-path event under `runtime-sensitive-file` and a socket event
under `runtime-network-connect` matching the printed destination. Writes used
to create the fake file can also produce file events; distinguish permission
masks rather than assuming one event per probe. The control path should not
appear in that file policy's events. Its string can still appear in other
records, so do not use a global text search as the negative assertion.

Verify the exported Docker prefix against Juice Shop's full container ID and
compare live cgroup membership using the container's main host PID. Retain
sanitized sample schemas for the next step. Repeat the existing process
acceptance check once the stack is ready.

Vector now maps both policies into `file` and `network` categories alongside
process lifecycle records. Unknown valid payloads remain `other` with their
original data preserved. See [the envelope](vector.md#event-and-metric-choices).
This raw probe alone does not establish storage or operational acceptance.

## Disable a policy

Move only the selected YAML file out of `collector/tetragon/policies/`, then
recreate the collector with the same Compose command and verify its policy
list. Startup policies reload from disk on recreation; edits alone do not
change an already running sensor. Preserve captures before cleanup and do not
delete event/checkpoint volumes to disable a policy.
