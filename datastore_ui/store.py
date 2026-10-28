"""Own the local database and publish immutable snapshots of append-only JSONL."""

import hashlib
import os
from pathlib import Path
import tempfile
import threading

import duckdb

from scripts.ingest_events import EVENT_TABLE, ingest_batch


VIEW = '''CREATE OR REPLACE VIEW security_events AS
    SELECT event_id, event_time, event_type, host_name, process_exec_id,
           container_id_prefix,
           coalesce(normalized_event->>'category', 'other') AS category,
           normalized_event->'process'->>'binary' AS executable,
           normalized_event->'process'->>'arguments' AS arguments,
           normalized_event->'file'->>'path' AS file_path,
           TRY_CAST(normalized_event->'file'->>'permission_mask' AS INTEGER) AS permission_mask,
           TRY_CAST(normalized_event->'file'->>'permission_result' AS INTEGER) AS permission_result,
           normalized_event->'network'->>'destination_ip' AS destination_ip,
           TRY_CAST(normalized_event->'network'->>'destination_port' AS INTEGER) AS destination_port,
           normalized_event->'network'->>'state' AS socket_state,
           normalized_event
    FROM events'''


def snapshot(source, directory):
    """Copy the initial complete-line prefix; later appends wait for next import."""
    directory.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as capture, tempfile.TemporaryDirectory(dir=directory) as temporary:
        before = os.fstat(capture.fileno())
        remaining = before.st_size
        partial = Path(temporary) / 'capture.jsonl'
        digest = hashlib.sha256()
        rows = 0
        with partial.open('wb') as output:
            while remaining:
                line = capture.readline(remaining)
                remaining -= len(line)
                if not line.endswith(b'\n'):
                    break
                output.write(line)
                digest.update(line)
                rows += bool(line.strip())
        after = os.fstat(capture.fileno())
        if after.st_size < before.st_size or source.stat().st_ino != before.st_ino:
            raise ValueError(f'Staging file was truncated or replaced during capture: {source.name}')
        if not rows:
            return None
        completed = directory / f'{digest.hexdigest()}.jsonl'
        partial.chmod(0o600)
        try:
            os.link(partial, completed)
        except FileExistsError:
            pass
        return completed


class Store:
    """One cached owner per UI process; a lock also serializes browser sessions."""

    def __init__(self, source, directory):
        self.source = Path(source)
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / 'events.duckdb'
        self.lock = threading.Lock()
        with duckdb.connect(str(self.database), config={'threads': 1, 'memory_limit': '128MB'}) as connection:
            connection.execute(f'CREATE TABLE IF NOT EXISTS events ({EVENT_TABLE}, PRIMARY KEY (event_id))')
            connection.execute(VIEW)

    def import_latest(self):
        results = []
        with self.lock:
            for source in sorted(self.source.glob('*.jsonl')):
                completed = snapshot(source, self.directory / 'captures')
                if completed is not None:
                    results.append(ingest_batch(completed, self.directory / 'parquet', self.database))
        return results

    def query(self, sql, parameters=None):
        """Return up to 500 rows from one SELECT, with local file access disabled."""
        with self.lock, duckdb.connect(str(self.database), read_only=True, config={
                'threads': 1, 'memory_limit': '128MB', 'enable_external_access': False,
                'autoload_known_extensions': False, 'autoinstall_known_extensions': False}) as connection:
            statements = connection.extract_statements(sql)
            if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
                raise ValueError('Enter one SELECT query. Database changes are disabled.')
            timer = threading.Timer(5, connection.interrupt)
            timer.start()
            try:
                result = connection.execute(sql, parameters or [])
                columns = [column[0] for column in result.description]
                rows = result.fetchmany(501)
                return columns, rows[:500], len(rows) > 500
            finally:
                timer.cancel()
                timer.join()
