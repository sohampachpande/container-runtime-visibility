# Container Runtime Visibility

## Motivation / Goal

Build a runtime visibility agent and pipeline to monitor agentic workflows at
the Linux kernel level: which processes they launch, which sensitive files they
access, and where they attempt to connect.

The motivation came from the [Hugging Face incident](https://openai.com/index/hugging-face-incident-and-the-road-ahead/),
in which agents repurposed JFrog Artifactory as an unauthorized message board
to communicate and coordinate. It raised a practical question: how can we
observe the system activity behind an agent's actions?

This project builds on my experience with OpenTelemetry (OTel), adding kernel
and container telemetry as another source of visibility. The longer-term goal
is to connect that activity to agent runs and tool calls. That correlation is
future work; the current lab uses OWASP Juice Shop as its demo workload.

## What We Built

```text
Docker Desktop's Linux VM
  │
  Tetragon ── raw JSON logs ──► Vector
                                 │
                         normalized JSONL
                         shared Docker volume
                                 │ read-only mount
                                 ▼
                        datastore-ui container
                         "Import latest" button
                                 │
                      snapshot → Parquet → DuckDB
                                              │
                                       Streamlit UI / SQL
```

1. **Kernel collection:** Tetragon observes process execution/exit,
   sensitive-file permission checks, and outbound TCP connection attempts.
2. **Event pipeline:** Vector normalizes events into a shared schema, preserves
   original payloads, filters process events to the selected workload, and
   exposes counters with bounded metric labels.
3. **Local storage:** completed JSONL snapshots become compressed Parquet and
   native DuckDB tables. Validation runs in bounded batches; repeated imports
   do not duplicate database records.
4. **Investigation UI:** a separate container owns DuckDB and provides filters,
   charts, original-event inspection, grouped process lifecycles, and a
   read-only SQL editor.

## Status

**Milestone 4 is complete. Milestone 5's core pipeline works; operational
acceptance remains in progress.** See [milestones and verified evidence](docs/milestone.md).

| Area | Current state |
| --- | --- |
| End-to-end pipeline | All three event categories verified through Tetragon, Vector, Parquet, DuckDB, and the dashboard on Docker Desktop. |
| Collection coverage | File/network policies target `/nodejs/bin/node`; file paths must start with `/tmp/runtime-visibility-sensitive-`. File checks and TCP attempts do not prove completed reads or successful connections. |
| Workload selection | Process filtering uses a manually selected container ID. Recreation requires refreshing it; stale IDs can silently exclude new process events. [Assumption and follow-up](docs/process-noise.md#known-assumption-manually-maintained-container-identity). |
| Operational work | Imports are manual. Automatic workload discovery, retention/disk budgets, sustained-load testing, and crash/full-disk recovery remain pending. |
| Agent integration | Agent-run/OTel correlation, detections, alerts, and an application API are future work. Collection is observation-only. |

**Try the lab:** [Docker Desktop setup](docs/docker-desktop.md) ·
[Dashboard and SQL](docs/datastore-ui.md) ·
[Datastore design](docs/datastore.md).

## Copyright

Copyright 2026 Soham Pachpande. All rights reserved.

No license is granted for reuse of this project's original code or documentation.
Third-party components retain their respective licenses.
