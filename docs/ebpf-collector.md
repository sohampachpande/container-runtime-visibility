# eBPF Security Telemetry Collection

## Goal

Use Tetragon as the host-level eBPF collector for kernel events relevant to runtime security and container health. OOM kills are one event type, not the product boundary.

The project focuses on learning eBPF event semantics, runtime visibility, and security analysis. Tetragon's structured JSON and gRPC events retain process and workload context while its tracing policies expose focused kernel hooks. The project builds the normalization, pipeline, attribution, and detection layers around those events. It is observability-only in its first versions; kernel-level enforcement is a later, separate decision.

## Architecture

The collection path has three layers:

1. Tetragon attaches eBPF programs and emits process, syscall, file, network, and other supported runtime events.
2. Vector receives collector output, normalizes event fields, and routes structured events and low-cardinality metrics.
3. A workload-analysis layer resolves or validates cgroup identity against Docker containers now and Kubernetes workloads later.

The normalized event envelope will carry a timestamp, event type, host PID and TGID, process name, UID, and cgroup identity. Event-specific fields are added only when they are available from the selected collector and hook.

## First event source: process execution

Start with the collector's process-execution events. Unlike an OOM victim, the process is alive when this event is emitted, so its cgroup identity can be validated directly and used to establish reliable container attribution.

Milestone 3 first verifies a raw JSON execution event from a live host process
and checks its host PID, process name, and available cgroup identity. Vector
normalization and demo-container attribution follow in later milestones.

Use [the Docker Desktop guide](docker-desktop.md) for local deployment
and live-process verification.

## Subsequent event sources

Add each source through the same event pipeline:

1. Sensitive-file observations selected from supported policies and hooks.
2. Outbound TCP connection attempts, with process and available workload identity.
3. Additional security events after the first two sources are validated.

OOM collection is outside Milestone 5; the original debugging exercise remains.
See [the action plan](../plans/milestone-5-datastore-plan.md).

The capability probe and collector startup output determine which optional event sources are available. The pipeline should report unsupported modules rather than assume every Linux host has identical BPF features.

## Choosing the collection scope

Separate three decisions: which workload to observe, which kernel operations
to record, and which observations a later rule considers suspicious. Monitoring
an entire container means covering its processes for selected operations; it
does not require recording every syscall or every file operation.

| Scope | Useful for | Limitation |
|---|---|---|
| Executable path | A focused program or MVP probe | Misses other executables and tool subprocesses |
| PID/process tree | A temporary investigation of one running application | PIDs change or are reused; a process tree need not cover the container |
| UID/credentials | Activity performed under selected credentials | One UID can be shared by unrelated containers; credentials can change |
| Workload/container identity | Covering the selected workload across executable changes | Needs verified runtime integration and lifecycle handling |

For broader security monitoring, prefer a verified workload boundary and
selective operations: execution events, connection attempts, and protected-path
access. An exhaustive binary list is fragile because unexpected executables
are often the activity we want to discover. Filtering in the kernel reduces
export volume; Vector filtering occurs after capture/export costs are paid.

### Current Node.js MVP

Both new file/network policies use `matchBinaries` for `/nodejs/bin/node`.
Python, other Node.js paths, and different executables launched by Node.js are
excluded. The base execution/exit sensor remains independent of those filters.
This is our policy choice, not a language limitation of Tetragon. A different
container using the same executable can match, so verify exported workload
identity. The file policy also requires the protected probe-path prefix.
See [the coverage table](security-events.md#why-the-mvp-filters-for-nodejs).

### PID filtering

Tetragon v1.7.0 provides `matchPIDs` with `In`/`NotIn`, `isNamespacePID`, and
`followForks`. This illustrative selector matches host PID 12345 and its
descendants; it has not been applied to our policies:

```yaml
selectors:
  - matchPIDs:
      - operator: In
        values: [12345]
        isNamespacePID: false
        followForks: true
```

Container PID 1 alone is not a unique workload identity: it appears in multiple
PID namespaces. Host PIDs change on restart and can be reused. Processes started
through `docker exec` need not descend from the application, so following its
children is not equivalent to container-wide coverage. Tetragon also offers
`matchBinaries.followChildren`, with documented limits such as not matching
children created before the policy was installed.
[Pinned PID API](https://github.com/cilium/tetragon/blob/v1.7.0/pkg/k8s/apis/cilium.io/v1alpha1/types.go#L203-L216)
and [selector documentation](https://tetragon.io/docs/concepts/tracing-policy/selectors/).

### UID filtering

UID answers which credentials a process uses; it does not identify a container.
Two containers can both use UID 1000 or 65532, and one container can contain
several users. Distinguish real and effective UID and account for credential
changes. User namespaces can map a container-visible UID to a different host
UID, so do not assume values from inside the container equal kernel-level values.
[Docker user-namespace mapping](https://docs.docker.com/engine/security/userns-remap/).

There is no dedicated `matchUIDs` field in the pinned selector API inspected.
Tetragon documents extracting credential fields from `current_task` with `data`
and filtering them with `matchData`; for example, `cred.uid.val` is a real UID
field. Validate the desired credential field, hook, BTF layout, and pinned
version on the actual kernel before applying that approach. Filtering an
exported UID in Vector is another option, but cannot reduce kernel export cost.
[Credential-data filtering](https://tetragon.io/docs/concepts/tracing-policy/selectors/#data-filter).

### Workload-wide filtering

Kubernetes-aware Tetragon deployments can select pods and containers using
workload metadata, subject to runtime integration. Our standalone Docker lab
must independently validate supported runtime-identity or namespace selection.
Namespaces can be shared and their numeric identities can change on recreation;
do not treat any namespace number as a permanent container ID.
[Workload selection](https://tetragon.io/docs/concepts/tracing-policy/k8s-filtering/).

Keep executable/PID/UID filters available for focused investigations. Test
workload-wide coverage with alternate binaries, child processes, and injected
executions before claiming that every relevant process is covered.

## Deployment model

Do not place this agent in an application image base layer. A host collector needs host kernel access; embedding it in every workload would create duplicate privileged observers and incomplete node coverage.

For Docker, package it as a dedicated agent image that runs once per host with the host paths and capabilities required by the selected BPF programs.

For Kubernetes, package the same agent image as a DaemonSet so one instance runs on each eligible node. Begin with a privileged development deployment, then replace it with the smallest verified capability and host-mount set after the required probes are known.
