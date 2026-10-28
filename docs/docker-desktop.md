# Local Docker Desktop lab

Docker Desktop is the default environment for ongoing development on
`m5/datastore`. Always combine `compose.yaml` and `compose.desktop.yaml` with
project name `runtime-visibility-desktop`, as shown below. The GCP VM remains
available for native Linux validation.

## What is observed

The stack can observe Docker Desktop's Linux VM kernel. It cannot observe
macOS kernel activity. On 2026-10-25, the local `desktop-linux` context was
verified with ARM64 kernel `7.0.14-linuxkit`, cgroup v2, readable BTF, about
4 GiB VM RAM, and Docker Compose `5.6.0`.

Tetragon's base BPF sensor loaded successfully. Fresh Linux VM and Juice Shop
executions passed the complete raw-event, normalization, container identity,
host-name, and counter-label checks. All four Vector tests also passed.
See Tetragon's [Docker Desktop guidance](https://tetragon.io/docs/installation/faq/#can-i-run-tetragon-on-mac-computers).
Support depends on the actual VM kernel and its BPF/BTF features.

## Desktop-specific configuration

Use the root `compose.yaml` with `compose.desktop.yaml`. The override changes
only the storage and BTF access needed for Desktop:

- Tetragon reads `/sys/kernel/btf/vmlinux` from its privileged container, which
  exposes the Linux VM kernel's BTF. There is no macOS `/sys` bind mount.
- Tetragon and Vector share raw events through a Docker named volume instead
  of a host `/var/log/tetragon` bind mount.
- Read checkpoints use `runtime-visibility-desktop-vector-data`, separate
  from the GCP lab's `vector-m4-data` volume.

Images, process privileges, namespaces, configuration drop-ins, memory limits,
and loopback ports are inherited from the root file. The override uses
[`!override`](https://docs.docker.com/reference/compose-file/merge/#replace-value),
which requires Compose 2.24.4 or newer. The original root configuration
continues to be the native Linux/GCP deployment.

## Start and validate

Run from the repository root on the Mac. Confirm that Docker targets the local
Desktop daemon before starting the lab:

```sh
docker context show
docker info --format 'Kernel={{.KernelVersion}} Arch={{.Architecture}}'
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml config --quiet
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml pull
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml run --rm --no-deps vector \
  validate --no-environment /etc/vector/vector.toml
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml run --rm --no-deps vector \
  test /etc/vector/vector.toml
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml up --detach
```

No manual host log-directory or checkpoint-volume setup is needed for this
override. Wait for Tetragon's successful sensor loading and `Listening for
events...`, and for Vector to begin watching the raw event file.

## Repeat the live acceptance check

The existing check below validates Milestone 4 execution/exit telemetry.
For the new Milestone 5 policy-loading and file/network probes, follow
[the security-event guide](security-events.md). Those probes are not yet
part of the existing acceptance script.

```sh
docker pull ubuntu:24.04
python3 scripts/verify-pipeline.py --desktop
```

The script uses a temporary privileged Ubuntu helper with `nsenter` to create
a process in the Linux VM's mount, PID, UTS, network, and IPC namespaces. It
does not use a macOS `sleep` as its test process. The helper remains in its
container cgroup, so its event can still contain a Docker identity prefix.
That is independent of the namespaces it has joined.

A second probe executes Node.js inside Juice Shop. Raw events and Linux
`/proc` evidence are read through the Tetragon container, so the check needs
Docker access rather than access to a macOS `/var/log/tetragon` path. Expected
output includes `observed_node: docker-desktop` and the LinuxKit kernel version.
The temporary helper exits and is removed; the lab containers stay running.
The revised check snapshots retained raw-record fingerprints before probing,
then searches the active file and timestamped rotated `.log` backups for new
records. It avoids reusing an offset after rotation, but scans retained logs on
each poll and is intended for this bounded lab, not high-volume operation.
The revised reader passed a live execution/exit acceptance run on 2026-10-27.
Both additional tracing policies and the all-category storage path passed
their separate checks; see [the storage evidence](datastore-ingestion.md#verification-and-limits).

## Inspect output and stop

The optional [query dashboard](datastore-ui.md) adds a fourth container using
the `ui` profile. It reads Vector's staging volume read-only and owns a separate
datastore volume. Include `--profile ui` when managing the full stack with it.

```sh
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml logs --follow vector
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml exec -T tetragon \
  tail -f /var/log/tetragon/tetragon.log
curl --fail http://127.0.0.1:9598/metrics
curl --fail --silent --output /dev/null http://127.0.0.1:3000/
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml stats --no-stream
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml down
```

Press Ctrl-C to stop following logs before continuing. Teardown without `-v`
retains local raw events and checkpoints. The test used loopback ports 3000
and 9598; they must be available on the Mac.

Measured steady-state container memory was about 75 MiB for Tetragon, 41 MiB
for Vector, and 102 MiB for Juice Shop. All three were running with no OOM
kills or automatic restarts. These observations apply to this tested Desktop
kernel and do not replace validation on a different host.
