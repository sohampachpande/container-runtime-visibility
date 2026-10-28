#!/usr/bin/env python3
"""Verify the root Compose pipeline using fresh host and Juice Shop events."""

import argparse
import datetime
import hashlib
import json
import re
import subprocess
import time
import urllib.request
import uuid


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, stderr=subprocess.DEVNULL)


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--desktop', action='store_true', help='Test Docker Desktop Linux VM using compose.desktop.yaml')
args = parser.parse_args()
compose = ['compose']
if args.desktop:
    compose += ['-p', 'runtime-visibility-desktop', '-f', 'compose.yaml', '-f', 'compose.desktop.yaml']

vector_id = docker(*compose, 'ps', '--quiet', 'vector').strip()
juice_id = docker(*compose, 'ps', '--quiet', 'juice-shop').strip()
tetragon_id = docker(*compose, 'ps', '--quiet', 'tetragon').strip()
assert vector_id and juice_id and tetragon_id, 'Start the Compose stack before verification'
node = docker('exec', tetragon_id, 'hostname').strip()
kernel = docker('exec', tetragon_id, 'uname', '-r').strip()


def samples():
    with urllib.request.urlopen('http://127.0.0.1:9598/metrics', timeout=3) as response:
        text = response.read().decode()
    result = {}
    for line in text.splitlines():
        match = re.match(r'runtime_events_total\{([^}]*)\}\s+([0-9.eE+\-]+)', line)
        if match:
            labels = dict(re.findall(r'(\w+)="([^"]*)"', match[1]))
            assert set(labels) == {'host', 'event_type'}, labels
            result[(labels['host'], labels['event_type'])] = float(match[2])
    return result


for attempt in range(50):
    try:
        before = samples()
        break
    except OSError:
        time.sleep(0.2)
else:
    raise SystemExit('Vector metrics endpoint did not become ready')

started = datetime.datetime.now(datetime.timezone.utc).isoformat()
container = json.loads(docker('inspect', juice_id))[0]
marker = f'compose_pipeline_probe_{uuid.uuid4().hex}'
expected_raw = {}


def raw_events():
    output = docker('exec', tetragon_id, 'sh', '-c',
                    'for capture in /var/log/tetragon/tetragon.log /var/log/tetragon/tetragon-*.log; '
                    'do if [ -f "$capture" ]; then cat "$capture"; fi; done')
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        yield hashlib.sha256(line.encode()).hexdigest(), event


# Compare retained records rather than reusing a byte offset after rotation.
seen_raw = {fingerprint for fingerprint, event in raw_events()}
if args.desktop:
    host = subprocess.Popen([
        'docker', 'run', '--rm', '--privileged', '--pid=host', '--uts=host',
        '--cgroupns=host', 'ubuntu:24.04', 'nsenter', '-t', '1', '-m', '-u', '-n', '-i',
        'sh', '-c', 'sleep 2 & probe_pid=$!; echo "$probe_pid"; wait "$probe_pid"',
    ], stdout=subprocess.PIPE, text=True)
    host_pid = int(host.stdout.readline().strip())
else:
    host = subprocess.Popen(['/usr/bin/sleep', '2'])
    host_pid = host.pid
docker('exec', '--detach', juice_id, '/nodejs/bin/node', '-e',
       f'setTimeout(() => {{}}, 15000) // {marker}')
assert host.wait(timeout=10) == 0
deadline = time.monotonic() + 15
while time.monotonic() < deadline:
    for fingerprint, event in raw_events():
        if fingerprint in seen_raw:
            continue
        seen_raw.add(fingerprint)
        for event_type, payload in event.items():
            if event_type not in ('process_exec', 'process_exit'):
                continue
            process = payload['process']
            if process['pid'] == host_pid:
                expected_raw['host_exec' if event_type == 'process_exec' else 'host_exit'] = event
            if event_type == 'process_exec' and process.get('binary') == '/nodejs/bin/node' and marker in process.get('arguments', ''):
                prefix = process.get('docker', '')
                assert prefix and container['Id'].startswith(prefix)
                cgroup = docker('exec', tetragon_id, 'cat', f'/proc/{process["pid"]}/cgroup')
                assert container['Id'] in cgroup
                expected_raw['container_exec'] = event
    if len(expected_raw) == 3:
        break
    time.sleep(0.2)
assert len(expected_raw) == 3, list(expected_raw)

matched = {}
deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    for line in docker('logs', '--since', started, vector_id).splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        for name, raw in expected_raw.items():
            if event.get('tetragon') == raw:
                kind = 'process_exit' if name == 'host_exit' else 'process_exec'
                assert event['event_type'] == kind
                assert event['process'] == raw[kind]['process']
                assert event['host']['name'] == raw['node_name'] == node
                if name == 'container_exec':
                    assert event['container']['id_prefix'] == raw[kind]['process']['docker']
                matched[name] = {'host_pid': event['process']['pid'], 'event_type': kind,
                                 'binary': event['process']['binary'],
                                 'container_id_prefix': event['container']['id_prefix']}
    after = samples()
    counters_advanced = all(
        after.get((node, kind), 0) > before.get((node, kind), 0)
        for kind in ('process_exec', 'process_exit')
    )
    if len(matched) == 3 and counters_advanced:
        break
    time.sleep(0.2)
assert len(matched) == 3, matched
for kind in ('process_exec', 'process_exit'):
    key = (node, kind)
    assert after.get(key, 0) > before.get(key, 0), (kind, before, after)
print(json.dumps({'observed_node': node, 'observed_kernel': kernel, 'matched_events': matched, 'counter_deltas': {
    kind: after.get((node, kind), 0) - before.get((node, kind), 0)
    for kind in ('process_exec', 'process_exit')},
    'metric_label_keys': ['event_type', 'host']}, indent=2))
