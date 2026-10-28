"""Exercise snapshot boundaries, the persistent UI store, and query isolation."""

import json
from pathlib import Path
import tempfile
import unittest

import duckdb

from datastore_ui.store import Store, snapshot


def event(number, category='process'):
    kind = 'process_exec' if category == 'process' else 'process_kprobe'
    process = {'pid': number, 'exec_id': f'ui-test:{number}', 'binary': '/nodejs/bin/node'}
    return {'schema_version': 1, 'collector': 'tetragon', 'event_type': kind,
            'timestamp': '2026-10-28T12:00:00Z', 'host': {'name': 'test-host'},
            'category': category, 'process': process, 'container': {'id_prefix': 'workload'},
            'file': {'path': '/tmp/fake-credentials', 'permission_mask': 4, 'permission_result': 0} if category == 'file' else None,
            'network': {'destination_ip': '127.0.0.1', 'destination_port': 12345, 'state': 'TCP_SYN_SENT'} if category == 'network' else None,
            'tetragon': {'time': '2026-10-28T12:00:00Z', 'node_name': 'test-host', kind: {'process': process}}}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='runtime-visibility-ui-test-')
        self.root = Path(self.temporary.name)
        self.staging = self.root / 'staging'
        self.staging.mkdir()
        self.source = self.staging / 'events.jsonl'
        self.store = Store(self.staging, self.root / 'data')

    def tearDown(self):
        self.temporary.cleanup()

    def test_snapshot_defers_partial_tail_and_does_not_change_source(self):
        data = json.dumps(event(1)).encode() + b'\n' + json.dumps(event(2)).encode()[:50]
        self.source.write_bytes(data)
        completed = snapshot(self.source, self.root / 'snapshots')
        self.assertEqual(completed.read_bytes(), data.split(b'\n')[0] + b'\n')
        self.assertEqual(self.source.read_bytes(), data)
        self.assertEqual(snapshot(self.source, self.root / 'snapshots'), completed)
        self.assertEqual(completed.stat().st_mode & 0o777, 0o600)

    def test_empty_or_incomplete_only_source_does_not_publish(self):
        for data in (b'', b'{"partial":'):
            self.source.write_bytes(data)
            self.assertIsNone(snapshot(self.source, self.root / 'snapshots'))
        self.assertEqual(list((self.root / 'snapshots').iterdir()), [])

    def test_import_growth_retry_reopen_and_all_category_projection(self):
        events = [event(1), event(2, 'file'), event(3, 'network')]
        self.source.write_text(json.dumps(events[0]) + '\n')
        self.assertEqual(self.store.import_latest()[0]['inserted_rows'], 1)
        self.assertEqual(self.store.import_latest()[0]['inserted_rows'], 0)
        with self.source.open('a') as output:
            for record in events[1:]:
                output.write(json.dumps(record) + '\n')
        self.assertEqual(self.store.import_latest()[0]['inserted_rows'], 2)
        reopened = Store(self.staging, self.root / 'data')
        _, rows, _ = reopened.query('SELECT category, count(*) FROM security_events GROUP BY category ORDER BY category')
        self.assertEqual(rows, [('file', 1), ('network', 1), ('process', 1)])
        _, rows, _ = reopened.query("SELECT file_path, permission_mask, permission_result FROM security_events WHERE category = 'file'")
        self.assertEqual(rows, [('/tmp/fake-credentials', 4, 0)])
        _, rows, _ = reopened.query("SELECT destination_ip, destination_port, socket_state FROM security_events WHERE category = 'network'")
        self.assertEqual(rows, [('127.0.0.1', 12345, 'TCP_SYN_SENT')])
        _, rows, _ = reopened.query('SELECT normalized_event FROM events ORDER BY process_exec_id')
        self.assertEqual([json.loads(row[0]) for row in rows], events)

    def test_malformed_complete_row_is_not_silently_skipped(self):
        self.source.write_text(json.dumps(event(1)) + '\ninvalid JSON\n')
        with self.assertRaisesRegex(ValueError, ':2:'):
            self.store.import_latest()
        self.assertEqual(self.store.query('SELECT count(*) FROM events')[1], [(0,)])
        self.assertEqual(list((self.root / 'data' / 'parquet').glob('*.parquet')), [])

    def test_query_rejects_mutations_multiple_statements_and_local_file_access(self):
        for sql in ('DELETE FROM events', 'CREATE TABLE secret AS SELECT 1',
                    'SELECT 1; SELECT 2', "ATTACH '/tmp/other.duckdb' AS other"):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                self.store.query(sql)
        secret = self.root / 'secret.txt'
        secret.write_text('not telemetry')
        with self.assertRaises(duckdb.Error):
            self.store.query('SELECT * FROM read_text(?)', [str(secret)])
        self.assertEqual(secret.read_text(), 'not telemetry')

    def test_query_caps_returned_rows(self):
        columns, rows, truncated = self.store.query('SELECT * FROM range(1000) AS t(number)')
        self.assertEqual(columns, ['number'])
        self.assertEqual(len(rows), 500)
        self.assertTrue(truncated)

    def test_query_timeout_does_not_poison_next_query(self):
        with self.assertRaises(duckdb.InterruptException):
            self.store.query('SELECT sum(random()) FROM range(1000000000000)')
        self.assertEqual(self.store.query('SELECT 42')[1], [(42,)])


if __name__ == '__main__':
    unittest.main()
