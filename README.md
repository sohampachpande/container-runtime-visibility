# Container Runtime Visibility

An eBPF-assisted Linux runtime-security lab. Tetragon observes host and
container process activity, with OWASP Juice Shop as the security demo workload.

## First milestone: diagnose an OOM kill

The demo service intentionally allocates memory until Docker's cgroup memory limit kills it. The investigation connects three signals:

1. Docker reports the container's memory limit and exit status.
2. The application logs its allocation progress before it exits.
3. A later eBPF collector will observe the kernel OOM event and attribute it to the container cgroup.

## Run locally with Docker Desktop

Run Tetragon, Vector, and OWASP Juice Shop as separate containers using
Docker Desktop's Linux VM kernel. Follow [the local development guide](docs/docker-desktop.md)
for prerequisites, validation, logs, and shutdown commands.

```sh
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml up --detach
python3 scripts/verify-pipeline.py --desktop
```

Open [Juice Shop](http://localhost:3000). Vector exposes event counters at
[localhost:9598/metrics](http://localhost:9598/metrics) and normalized JSON in
its container logs. Tetragon and Vector share raw events through a Docker
named volume. The collector observes the Linux VM kernel, not macOS.

For charts, event inspection, and a read-only SQL editor, start the optional
[DuckDB dashboard](docs/datastore-ui.md) in its own container:

```sh
docker compose -p runtime-visibility-desktop \
  -f compose.yaml -f compose.desktop.yaml --profile ui \
  up --detach --build datastore-ui
```

Open [the dashboard](http://127.0.0.1:8501) and choose **Import latest**.
It reads Vector's staging volume without stopping the pipeline, then stores
snapshots, Parquet, and its database on a separate persistent volume.

## Original OOM exercise

The Python workload in `test-app/` remains available separately through
`compose.oom.yaml`:

```sh
docker compose -f compose.oom.yaml up --build --detach
curl http://localhost:8080/healthz
curl 'http://localhost:8080/allocate?mb=1024'
```

Its 64 MiB limit and diagnostic steps are documented in
[the Docker OOM workflow](docs/debugging-workflow.md). Stop Juice Shop before
running the OOM exercise if memory is constrained.

## Linux host requirement

The eBPF milestone needs a Linux kernel with BTF support and permission to load
tracing programs. `bpftool` is an optional diagnostic aid. Docker Desktop on
macOS runs its own Linux VM. The GCP Linux deployment and the local
[Docker Desktop deployment](docs/docker-desktop.md) are both verified; neither
collects events from the macOS kernel.

## Debugging workflow

The earlier [GCP VM setup](archive/README.md) is archived.

Follow [the Docker OOM debugging workflow](docs/debugging-workflow.md) to reproduce the incident and diagnose it with application logs and Docker container state.

## Collector and pipeline status

Tetragon process execution and Docker container identity are verified on
Docker Desktop. See [the collector guide](docs/tetragon.md). Raw cgroup enrichment is
deferred, and additional event coverage follows incrementally.

Milestone 4's [Vector pipeline](docs/vector.md) reads Tetragon JSON, normalizes
process events, and exposes a low-cardinality event counter. File ingestion,
normalization, and counter labels are verified on Docker Desktop; a Prometheus rate
query remains unverified.

The root Compose definition is the lab entry point; all three containers and
the combined Linux pipeline have passed live validation.
