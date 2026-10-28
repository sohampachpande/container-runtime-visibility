"""Run UI interaction checks when the optional Streamlit dependency is installed."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_datastore_ui import event


@unittest.skipUnless(importlib.util.find_spec('streamlit'), 'Install datastore_ui/requirements.txt for UI checks')
class DashboardTests(unittest.TestCase):
    def test_empty_state_and_import_query_flow(self):
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory(prefix='runtime-visibility-dashboard-test-') as directory:
            root = Path(directory)
            source = root / 'staging'
            source.mkdir()
            with patch.dict(os.environ, {'STAGING_DIR': str(source), 'DATASTORE_DIR': str(root / 'data')}):
                app = AppTest.from_file('datastore_ui/app.py', default_timeout=15).run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(any('No imported events' in message.value for message in app.info))
                records = [event(1), event(2, 'file'), event(3, 'network')]
                (source / 'events.jsonl').write_text(''.join(json.dumps(record) + '\n' for record in records))
                next(button for button in app.button if button.label == 'Import latest').click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual([metric.value for metric in app.metric], ['1', '1', '1', '0'])
                app.radio[0].set_value('SQL').run()
                app.text_area[0].set_value('SELECT category, count(*) AS records FROM security_events GROUP BY category ORDER BY category')
                next(button for button in app.button if button.label == 'Run query').click().run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(app.dataframe[-1].value['category'].tolist(), ['file', 'network', 'process'])
                self.assertEqual(app.radio[0].value, 'SQL')
                app.text_area[0].set_value('DELETE FROM events')
                next(button for button in app.button if button.label == 'Run query').click().run()
                self.assertTrue(any('one SELECT' in error.value for error in app.error))
                self.assertEqual(len(app.exception), 0)
