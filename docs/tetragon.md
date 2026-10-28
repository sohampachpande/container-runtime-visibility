# Tetragon collector

## Deployment and status

The root `compose.yaml` runs one dedicated Tetragon container on the Linux
host alongside Vector and Juice Shop. It uses `quay.io/cilium/tetragon:v1.7.0`;
no source build or native package installation is required. See
[the Docker Desktop guide](docker-desktop.md) for setup, validation, startup, and inspection.

The containerized collector verified process execution, exit, and Docker
container identity on the GCP VM on 2026-10-25. Its base BPF sensor loaded
successfully, and exactly one collector process was active. Raw cgroup ID/path
enrichment remains deferred. See [the milestone evidence](milestone.md).

## Host access and event export

The collector container loads eBPF programs into the Linux host kernel. Host
PID and cgroup namespaces expose host processes and cgroup membership; the
host BTF file supports loading CO-RE programs. Host UTS sharing preserves the
host name in exported events. The initial development deployment uses
privileged mode, following the upstream
[container instructions](https://tetragon.io/docs/installation/container/).
The collector targets a Linux kernel: either the GCP VM or Docker Desktop's
Linux VM using [the Desktop override](docker-desktop.md). It does not trace
the macOS kernel.

Repository drop-ins under `collector/tetragon/tetragon.conf.d/` are mounted
read-only into `/etc/tetragon/tetragon.conf.d/`. They configure JSON export,
permissions, rotation, Kubernetes lookup, and local API endpoints. The image
provides its own daemon, CLI, and BPF programs; it does not use the native
package's `/usr/local/lib/tetragon/` files.

Raw JSON is written to the host `/var/log/tetragon/tetragon.log`. Vector mounts
that same directory read-only. The Tetragon container's gRPC Unix socket and
loopback health endpoint are internal; no Tetragon API port is published.
Only one collector should run on the node.

```sh
sudo docker compose logs --tail 100 tetragon
sudo tail -f /var/log/tetragon/tetragon.log
sudo docker compose exec tetragon tetra \
  --server-address unix:///var/run/tetragon/tetragon.sock getevents -o compact
```

The first command shows diagnostics, the second shows the original JSON
export, and the third displays a compact live event stream. Press Ctrl-C to
stop following events.

## How process collection works

Process execution and exit collection are built into Tetragon's base sensor.
Starting the daemon loads this sensor; no custom tracing policy was needed for
the events we captured. The service journal confirmed
`Loaded sensor successfully` for `__base__`.

```text
Compose starts the Tetragon container
  → Tetragon loads its built-in base eBPF sensor
  → sensor observes process execution and termination
  → Tetragon adds process, parent, and available container context
  → JSON exporter writes /var/log/tetragon/tetragon.log
```

`process_exec` means a process executed a program. It does not always mean a
new process was created: Linux can replace a process's program through `exec`
while retaining its PID. `process_exit` records termination.

We are not recording every system call. A command such as `cat /etc/hostname`
can open a file, read bytes, write output, and close descriptors. The current
setup observes program execution and exit, not each of those file operations.
One JSON record does not necessarily correspond to one system call.

The `tetragon.conf.d` drop-ins configure daemon and export behavior, including
the JSON filename, permissions, rotation, and API endpoints. We have not set
an export event allowlist, so execution and exit events from the base sensor
are exported. Tetragon's versioned API defines their JSON structure; these
drop-ins do not define a custom event schema.

## Expanding collection with tracing policies

A `TracingPolicy` adds specific observations alongside the base sensor. It
selects a hook point, arguments or return values to capture, optional filters
called selectors, and actions. Tetragon loads and attaches the eBPF programs;
we do not need to write a new general-purpose collector.

Kprobes attach to kernel functions, tracepoints attach to predefined kernel
events, and uprobes attach to userspace functions. Tracepoints generally offer
better portability; kernel function names and signatures can change between
versions. See [hook points](https://tetragon.io/docs/concepts/tracing-policy/hooks/).

```text
Kernel reaches selected hook
  → eBPF captures selected data
  → selectors filter observations in the kernel
  → matching observation is posted as a Tetragon event
  → JSON exporter writes the event
```

The default policy action is `Post`, which emits an event. Policies can also
perform enforcement, but this project remains observation-only. Despite their
Kubernetes-style YAML, policies can be loaded on the standalone VM through
`tetra` or startup policy files. The earlier verified deployment used no custom
policies. Milestone 5 now adds startup
file/network policies under `collector/tetragon/policies/`; loading and live
events are pending verification. See [the raw-event guide](security-events.md),
[tracing policies](https://tetragon.io/docs/concepts/tracing-policy/)
and [selectors](https://tetragon.io/docs/concepts/tracing-policy/selectors/).

| Coverage | Possible observations | What to validate |
|---|---|---|
| Files | Open attempts, reads, writes, deletion | Which operation the hook represents and whether it succeeded |
| Network | Connection attempts, acceptance, socket activity | Direction, endpoints, and outcome |
| Privileges and security | Credential changes, capability checks, namespace changes | Attempt, permission check, or completed change |
| OOM | A process selected as an OOM victim | Which field identifies the victim rather than the currently executing process |

These are coverage candidates, not promises about our current installation.
Check available hooks and argument layouts against the VM's kernel and
Tetragon version before adding each policy.

Collection and detection are separate. An observation establishes activity;
a detection interprets it against an expectation, such as an unexpected
destination or access to a sensitive path. Adding a policy does not make all
matching events suspicious.

Expand one focused policy at a time: choose the behavior, select a hook,
generate controlled activity, inspect the raw JSON, verify its meaning and
container identity, then integrate it with Vector. Broad file or network
tracing can increase noise and overhead on the small VM. Vector normalization
remains Milestone 4; further event coverage follows incrementally.

## Validate host and container identity

Use [the Desktop acceptance check](docker-desktop.md#repeat-the-live-acceptance-check)
after the collector reports successful base-sensor loading and is listening
for events. Compare a fresh host execution with `/proc`, then check a Node.js
execution inside Juice Shop against Docker's container ID.

Tetragon's `process.docker` is a container ID prefix. It is not a human-readable
container name or a raw cgroup ID. The earlier Python and Juice Shop tests
verified this prefix against Docker and live cgroup membership. An injected
`docker exec` process can have `in_init_tree: false` while still belonging to
the correct container.

A record flagged `procFS` is startup discovery of an existing process; use a
fresh execution to demonstrate a new kernel observation. Flags are debugging
metadata, not stable detection-rule inputs. Store raw captures outside Git.
