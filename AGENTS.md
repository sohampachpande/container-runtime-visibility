# Agent guide

## Working agreement

- This is a learning project. Implement one agreed step at a time and explain
  the core design principles, key choices, and relevant tradeoffs.
- State assumptions before implementation. Ask one concise question when an
  ambiguity materially changes the work; otherwise proceed within scope.
- The user wants to stay involved in host commands. Explain commands and their
  purpose before running them. When the user asks to run commands, execute them;
  otherwise provide commands for the user to run.
- Lead updates with the next action or result. Number multi-step work and
  distinguish verified results from plans and assumptions.
- Keep changes minimal and directly related to the request. Match existing style.
- Define a concrete success criterion and run appropriate checks. Report what
  passed and what remains unverified; prerequisites alone do not complete a milestone.
- Confirm destructive actions. Do not commit, push, change cloud resource size,
  or increase cloud costs unless requested.

## Git conventions

- New branches start with `feat/`, `chore/`, or `ops/`; do not use `codex/`.
- Inspect the current branch and working tree before making changes. Preserve
  existing user changes. The default branch is `main`.
- Never commit credentials, Terraform state, plans, local variable values, or
  runtime event captures containing sensitive data.

## Project and architecture

This learning lab collects Linux runtime-security telemetry for containers.
It observes process execution and exit, selected sensitive-file permission
checks, and outbound TCP connection attempts. It does not enforce policy.

The pipeline is Tetragon JSON events → Vector normalization, routing and
low-cardinality metrics → JSONL staging → Parquet → native DuckDB tables →
the optional Streamlit investigation UI. Retain process and workload context;
do not use PIDs or raw file paths as metric labels. Validate actual Tetragon
output before promising fields or coverage.

- Run one host-level Tetragon collector per Linux node, separate from workloads.
  Do not replace it with a custom general-purpose eBPF collector.
- Workload selection currently resolves one running Compose container ID.
  Refresh it after container replacement and recreate Vector and the UI.
  File and network policy executable filters are host-wide, not container filters.
- The bundled file/network policies match Juice Shop's `/nodejs/bin/node`; file
  matching is restricted to a disposable probe prefix. Other applications need
  suitable policy configuration and live validation.
- Automatic discovery, enforcement, anomaly detection and agent/OTel correlation
  are future work. Kubernetes is a future DaemonSet deployment, not the current
  target. ClickHouse is a possible future server if measured needs justify it.
- OOM collection is outside the current pipeline. Preserve the standalone
  exercise in `archive/oom/`.

## Repository map

- `compose.yaml`: Tetragon, Vector and optional datastore UI.
- `compose.desktop.yaml`: Docker Desktop Linux VM overrides.
- `compose.demo.yaml`: optional OWASP Juice Shop workload.
- `collector/tetragon/`: collector configuration and tracing policies.
- `collector/vector/vector.toml`: normalization, filters, routing and Vector tests.
- `scripts/select-workload.py`: select one Compose service container.
- `scripts/verify-pipeline.py`: Desktop/demo process-pipeline acceptance probe.
- `scripts/probe-security-events.js`: demo file/network probe.
- `scripts/ingest_events.py`, `datastore_ui/`, `queries/`: storage and UI.
- `tests/`: Python ingestion and dashboard tests.
- `docs/design.md`, `docs/milestone.md`: decisions, milestone scope and evidence.
- `docs/docker-desktop.md`, `docs/security-events.md`, `docs/process-noise.md`:
  setup, event coverage and workload-selection limitations.
- `docs/datastore.md`, `docs/datastore-ingestion.md`, `docs/datastore-ui.md`:
  storage architecture, ingestion and UI.
- `plans/milestone-5-datastore-plan.md`: prior implementation and acceptance gates.
- `archive/oom/`, `archive/terraform/`, `archive/docs/`: historical exercise,
  GCP provisioning and older documentation.

## Validation and environments

Docker Desktop is the default development environment. For the demo combine
`compose.yaml`, `compose.desktop.yaml` and `compose.demo.yaml` with project
name `runtime-visibility-desktop`; see `docs/docker-desktop.md`. Tetragon
observes Docker Desktop's Linux VM kernel, not macOS. Compose rendering checks
configuration only. `python3 scripts/verify-pipeline.py --desktop` tests process
telemetry against a running, selected demo; it does not test a fresh deployment
or file/network policies. See `docs/security-events.md` for those probes.

The Python suite is `python3 -m unittest discover -s tests` with the dependencies
in `datastore_ui/requirements.txt`. Vector has separate tests in its config.
Choose checks appropriate to the change and state any environment limitations.

The Terraform-managed GCP VM is available for native Linux validation. It is an
`e2-micro` in `us-west1-b`; access uses IAP and OS Login. Measure resource
pressure before proposing a larger, billable VM. Do not assume the entire lab
is free: public IPv4 and excess usage may incur charges. Preserve ignored local
Terraform state, plans, and variable files. The Raspberry Pi's recorded kernel
lacks BTF and is an OOM exercise host, not the current Tetragon test host.

Evidence and runtime state can change. Recheck the relevant documentation and
live state before claiming support or completion; BTF alone does not prove that
Tetragon loads successfully. Update affected documentation with implementation.
