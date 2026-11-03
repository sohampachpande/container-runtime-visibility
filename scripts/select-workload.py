#!/usr/bin/env python3
"""Select one running Compose service container for process telemetry and the UI."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop', action='store_true')
    parser.add_argument('service', nargs='?', default='juice-shop', help='Compose service name (default: juice-shop)')
    parser.add_argument('-f', '--file', action='append', dest='files', help='Compose file; repeat in merge order')
    args = parser.parse_args()
    compose = ['docker', 'compose']
    if args.desktop:
        compose += ['-p', 'runtime-visibility-desktop']
    files = args.files or (['compose.yaml', 'compose.desktop.yaml', 'compose.demo.yaml'] if args.desktop else ['compose.yaml', 'compose.demo.yaml'])
    for file in files:
        compose += ['-f', file]
    containers = subprocess.check_output([*compose, 'ps', '--quiet', args.service], text=True).splitlines()
    if len(containers) != 1:
        parser.exit(1, f'Expected exactly one running container for {args.service}; found {len(containers)}.\n')
    container = containers[0]
    details = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
    identity = details['Id']
    if not details['State']['Running'] or re.fullmatch('[a-f0-9]{64}', identity) is None:
        parser.exit(1, f'Expected one running {args.service} container with a full Docker ID.\n')
    destination = Path(os.environ.get('WORKLOAD_ENV_FILE', '/tmp/runtime-visibility-workload.env'))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=destination.parent, delete=False) as output:
        output.write(f'MONITORED_CONTAINER_ID={identity}\n')
        temporary = Path(output.name)
    temporary.replace(destination)
    print(f'Selected {args.service} ({identity[:12]}). Wrote {destination}.')
    print('Recreate Vector and the dashboard with Compose to apply the selection.')


if __name__ == '__main__':
    main()
