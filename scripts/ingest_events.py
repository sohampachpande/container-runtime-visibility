#!/usr/bin/env python3
"""Convert one completed normalized JSONL capture to Parquet and import DuckDB."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import duckdb


COLUMNS = {
    'event_id': 'VARCHAR',
    'event_time': 'TIMESTAMPTZ',
    'schema_version': 'BIGINT',
    'event_type': 'VARCHAR',
    'host_name': 'VARCHAR',
    'process_exec_id': 'VARCHAR',
    'container_id_prefix': 'VARCHAR',
    'normalized_event': 'JSON',
}
EVENT_TABLE = ', '.join(f'{name} {kind}' for name, kind in COLUMNS.items())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def project(event):
    """Project the existing v1 envelope without interpreting new kernel payloads."""
    if not isinstance(event, dict) or type(event.get('schema_version')) is not int:
        raise ValueError('Expected a normalized event with integer schema_version')
    if event['schema_version'] != 1 or event.get('collector') != 'tetragon':
        raise ValueError('Only the current Tetragon schema_version 1 is supported')
    kind = event.get('event_type')
    if not isinstance(kind, str) or not kind:
        raise ValueError('event_type must be a nonempty string')
    raw = event.get('tetragon')
    if not isinstance(raw, dict) or not isinstance(raw.get(kind), dict):
        raise ValueError('tetragon must preserve the matching event payload')
    host = event.get('host')
    if not isinstance(host, dict) or not isinstance(host.get('name'), str) or not host['name']:
        raise ValueError('host.name must be a nonempty string')
    timestamp = event.get('timestamp')
    if not isinstance(timestamp, str):
        raise ValueError('timestamp must be an ISO-8601 string with timezone')
    if re.search(r'(Z|[+-]\d{2}:\d{2})$', timestamp) is None:
        raise ValueError('timestamp must include a timezone')
    process = event.get('process', {})
    container = event.get('container', {})
    process = {} if process is None else process
    container = {} if container is None else container
    if not isinstance(process, dict) or not isinstance(container, dict):
        raise ValueError('process and container must be objects or null')
    exec_id = process.get('exec_id')
    prefix = container.get('id_prefix')
    if any(value is not None and not isinstance(value, str) for value in (exec_id, prefix)):
        raise ValueError('Execution and container identities must be strings or null')
    return {
        'event_id': hashlib.sha256(canonical(raw).encode()).hexdigest(),
        # DuckDB parses RFC3339 fractions, including nanoseconds rejected by Python 3.9.
        'event_time': timestamp,
        'schema_version': event['schema_version'],
        'event_type': kind,
        'host_name': host['name'],
        'process_exec_id': exec_id,
        'container_id_prefix': prefix,
        'normalized_event': event,
    }


def validate_parquet(connection, parquet):
    difference = connection.execute('''
        SELECT count(*) FROM (
            (SELECT * FROM batch EXCEPT ALL SELECT * FROM read_parquet(?))
            UNION ALL
            (SELECT * FROM read_parquet(?) EXCEPT ALL SELECT * FROM batch)
        )
    ''', [str(parquet), str(parquet)]).fetchone()[0]
    if difference:
        raise ValueError(f'Parquet content does not match the completed capture: {parquet}')


def ingest_batch(source, parquet_dir, database):
    source, parquet_dir, database = map(Path, (source, parquet_dir, database))
    parquet_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    rows = 0
    with tempfile.TemporaryDirectory(prefix='.batch-', dir=parquet_dir) as directory:
        snapshot = Path(directory) / 'projected.jsonl'
        before = source.stat()
        with source.open('rb') as capture, snapshot.open('w', encoding='utf-8') as output:
            for number, line in enumerate(capture, 1):
                digest.update(line)
                if not line.strip():
                    continue
                try:
                    output.write(canonical(project(json.loads(line))) + '\n')
                except (ValueError, TypeError) as error:
                    raise ValueError(f'{source}:{number}: {error}') from error
                rows += 1
        after = source.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('Input changed during reading; provide a completed capture')
        if not rows:
            raise ValueError('Capture contains no events')
        batch_id = digest.hexdigest()
        parquet = parquet_dir / f'{batch_id}.parquet'
        with duckdb.connect(config={'threads': 1, 'memory_limit': '256MB',
                                    'temp_directory': str(Path(directory) / 'spill')}) as conversion:
            conversion.read_json(str(snapshot), columns=COLUMNS,
                                 format='newline_delimited').create('batch')
            if not parquet.exists():
                partial = Path(directory) / 'events.parquet'
                # COPY paths are SQL literals; escape quotes rather than interpolate SQL code.
                destination = "'" + str(partial).replace("'", "''") + "'"
                conversion.execute(f'COPY batch TO {destination} (FORMAT PARQUET, COMPRESSION ZSTD)')
                validate_parquet(conversion, partial)
                partial.chmod(0o600)
                try:
                    os.link(partial, parquet)  # Atomic publication without replacing an existing batch.
                except FileExistsError:
                    pass
            validate_parquet(conversion, parquet)

    database.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database), config={'threads': 1, 'memory_limit': '256MB'}) as connection:
        connection.execute('BEGIN TRANSACTION')
        try:
            connection.execute(f'CREATE TABLE IF NOT EXISTS events ({EVENT_TABLE}, PRIMARY KEY (event_id))')
            connection.execute('''CREATE TABLE IF NOT EXISTS imported_batches (
                batch_id VARCHAR PRIMARY KEY,
                parquet_name VARCHAR NOT NULL,
                source_rows BIGINT NOT NULL,
                inserted_rows BIGINT NOT NULL,
                imported_at TIMESTAMPTZ DEFAULT current_timestamp
            )''')
            existing = connection.execute(
                'SELECT batch_id FROM imported_batches WHERE batch_id = ?', [batch_id]).fetchone()
            inserted = 0
            if existing is None:
                count_before = connection.execute('SELECT count(*) FROM events').fetchone()[0]
                connection.execute('''INSERT INTO events
                    SELECT * FROM read_parquet(?)
                    QUALIFY row_number() OVER (PARTITION BY event_id) = 1
                    ON CONFLICT DO NOTHING''', [str(parquet)])
                inserted = connection.execute('SELECT count(*) FROM events').fetchone()[0] - count_before
                connection.execute('INSERT INTO imported_batches VALUES (?, ?, ?, ?, current_timestamp)',
                                   [batch_id, parquet.name, rows, inserted])
            connection.execute('COMMIT')
        except Exception:
            connection.execute('ROLLBACK')
            raise
    return {'batch_id': batch_id, 'parquet': str(parquet), 'database': str(database),
            'source_rows': rows, 'inserted_rows': inserted,
            'status': 'already_imported' if existing else 'imported'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path, help='Completed normalized JSONL, never an active log')
    parser.add_argument('--parquet-dir', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = ingest_batch(args.capture, args.parquet_dir, args.database)
    except (OSError, ValueError, duckdb.Error) as error:
        parser.exit(1, f'Import failed: {error}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
