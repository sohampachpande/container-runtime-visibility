# Design: Runtime Security Telemetry Agent

## Decision

Use Tetragon as the host-level eBPF collector and focus this project on understanding eBPF events, runtime visibility, security signals, and anomaly detection.

The collector supplies broad kernel event coverage. This project owns the event schema, Vector pipeline, workload-oriented analysis, and security detections. That keeps the learning focus on interpreting and using eBPF telemetry without making a general-purpose collector implementation the central deliverable.

The collector will run once per Linux node. It will first observe the Docker Compose demo workload and later be packaged as a dedicated deployment for Kubernetes.

## Test environment

The current development environment is Docker Desktop's Linux VM. Combine
`compose.yaml` and `compose.desktop.yaml` to run Tetragon, Vector, and Juice
Shop. The collector observes the VM's Linux kernel, not macOS. See
[the local development guide](docker-desktop.md) for verified kernel details
and commands.

The earlier [GCP VM setup](../archive/README.md) is archived. The Raspberry Pi
5 remains an OOM demonstration host; its current kernel lacks BTF.

## Collection strategy

Tetragon is the selected collector because it provides structured process, file, network, and kernel tracing events with workload-aware metadata. Its JSON and gRPC event streams preserve the underlying runtime context for Vector and later analysis. Its tracing policies can also select arbitrary kernel hook points and filter events in kernel.

Falco is deliberately out of scope for this project. Its rule-first detection engine and default security rules provide more detection behavior than is useful at this stage, when the project needs to learn from raw runtime telemetry and build its own pipeline and detections.

The project does not build a general-purpose collector in the initial scope. Small custom eBPF programs may still be used as focused learning exercises when a hook or event needs deeper investigation.

Tracee is not part of the planned collector path because the project preference is to use CNCF-aligned tools.

## Event model

The agent emits normalized runtime-security events as structured JSON. Vector receives those events, performs pipeline-level transformation and routing, and derives low-cardinality metrics.

Each event should include:

- timestamp
- event type
- host PID and TGID
- process name
- UID
- cgroup identity
- Docker or Kubernetes workload identity when available

Raw kernel events are logs. Aggregated event rates, error counts, and anomaly scores are metrics. Application traces remain a separate signal that can be correlated later.

## Event pipeline

~~~text
Selected eBPF collector
  → JSON runtime-security events
  → Vector
      → JSONL staging → Parquet batches → native DuckDB tables → SQL
      → low-cardinality metrics
~~~

Vector is the initial event-pipeline component. It keeps collection, normalization, routing, and metric derivation separate from kernel-hook code.
The [datastore decision](datastore.md) selects local JSONL staging, Parquet batch
conversion, and import into native DuckDB tables for analytical queries.
The initial schema-v1 Parquet/DuckDB batch importer is implemented and tested
offline; new event mappings and live storage validation remain pending.
ClickHouse is the preferred next step if a shared analytical service is needed.

```mermaid
flowchart LR
    K[Linux kernel events] --> A[Selected eBPF collector]
    A --> J[Structured JSON events]
    J --> V[Vector]
    V --> L[Normalized JSONL staging]
    L --> P[Completed Parquet batches]
    P --> Q[Native DuckDB tables and SQL]
    V --> M[Low-cardinality event metrics]
    M --> D[Future anomaly detection]
```

## Initial scope

Start with Tetragon process-execution events. This proves the complete collection path while the observed process is still alive, allowing reliable cgroup and workload attribution.

Add event sources incrementally:

1. Sensitive-file access
2. Outbound TCP connection observations
3. Additional security events after these sources are validated

OOM collection is outside Milestone 5; the original standalone debugging
exercise remains available.

The agent reports unavailable optional event sources at startup instead of assuming every Linux kernel exposes the same hooks.

## Anomaly detection

Begin with explainable statistical methods over low-cardinality event-rate series:

1. Fixed thresholds for known-sensitive behavior
2. Rolling mean and standard deviation for rate spikes
3. EWMA for gradual shifts
4. CUSUM for persistent small changes
5. Rarity scoring for first-seen executable, destination, or capability combinations

Use workload, namespace, event type, and destination class as detector keys. Do not create per-PID or raw-path metric series.

The initial project does not include a platform-backed anomaly engine. Start with event collection and the Vector pipeline, then add statistical detection after the event schema and baseline event rates are understood.

OpenSearch was considered and not selected for the current SQL analytical
workflow. DuckDB is the initial query engine, with ClickHouse as the preferred
next step if measured ingestion or query-serving needs require it. The optional
[local dashboard](datastore-ui.md) adds charts and a read-only SQL editor in a
separate container; alerting and a custom application API remain future work.

## Long-term target: AI agent runtime monitoring

The long-term use case is detecting unexpected data and network access by an AI agent.

Kernel events establish what the agent workload actually did: which process executed, which paths it accessed, and which network endpoints it contacted. They cannot establish the agent's task, selected tool, or the meaning of encrypted application traffic by themselves.

Pair the kernel agent with application-level agent telemetry:

1. The AI agent emits run ID, agent ID and version, tool name, task category, and trace context.
2. The kernel agent emits process, file, network, and cgroup events.
3. The pipeline correlates signals by workload identity and time. A later context bridge can map an active agent run ID to its process or cgroup.

Do not collect raw prompts, tool arguments, or file contents by default. They can contain sensitive data and are not required for the first behavior baselines.

The first AI-agent detections should combine explicit policy with statistical baselines:

1. First-seen or disallowed outbound destination for an agent workload.
2. First-seen or sensitive file-access path.
3. A tool invocation followed by a kernel action outside that tool's expected access class.
4. Sustained growth in outbound connections, bytes, or file-access events.

Network connections provide IP address and port at the kernel layer. Domain-level attribution requires a DNS signal, proxy telemetry, or application instrumentation. File-access events provide paths; sensitivity classification comes from policy or path classes, not the kernel event alone.

## Non-goals

The first versions do not enforce security policy. Kernel-level enforcement is a later decision after the event model, detection quality, and deployment permissions are understood.
