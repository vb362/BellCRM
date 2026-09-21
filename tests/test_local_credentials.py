"""Public builds need no credential; authorized local links use runtime config."""
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server


class LocalCredentialTests(unittest.TestCase):
    def test_missing_key_leaves_links_unconfigured(self):
        with patch.dict(os.environ, {'BELLHAVEN_API_TOKEN': ''}):
            self.assertIsNone(server.local_crm_link_base())

    def test_local_key_is_encoded_as_one_url_segment(self):
        with patch.dict(os.environ, {'BELLHAVEN_API_TOKEN': 'local/key?#" value'}):
            self.assertEqual(server.local_crm_link_base(),
                'https://analyst-assessment-production.up.railway.app/crm/local%2Fkey%3F%23%22%20value/accounts/')

    def test_env_file_loads_locally_and_environment_override_wins(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / '.env'
            config.write_text('BELLHAVEN_API_TOKEN="local-test-key"\n')
            with patch.dict(os.environ, {'BELLHAVEN_API_TOKEN': ''}):
                server.load_local_config(config)
                self.assertIn('/local-test-key/accounts/', server.local_crm_link_base())
            with patch.dict(os.environ, {'BELLHAVEN_API_TOKEN': 'environment-test-key'}):
                server.load_local_config(config)
                self.assertIn('/environment-test-key/accounts/', server.local_crm_link_base())

    def test_links_are_supplied_only_in_authenticated_local_state_not_static_html(self):
        with tempfile.TemporaryDirectory() as folder:
            demo = Path(folder) / 'demo.sqlite'
            with closing(sqlite3.connect(demo)) as c:
                c.executescript((server.ROOT / 'schema/working.sql').read_text())
                c.execute('PRAGMA user_version=4')
                c.execute("INSERT INTO crm_accounts(account_id,name) VALUES ('A','Sample facility')")
                c.commit()
            http = server.make_server(0, demo)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                base = f'http://127.0.0.1:{http.server_port}'
                def get_state(session=None, origin=None):
                    headers = {}
                    if session: headers['X-Bellhaven-Session'] = session
                    if origin: headers['Origin'] = origin
                    with urlopen(Request(base+'/api/state', headers=headers)) as response:
                        self.assertEqual(response.headers['Cache-Control'], 'no-store')
                        return json.load(response)
                with patch.dict(os.environ, {'BELLHAVEN_API_TOKEN': 'local-test-key'}), \
                        patch('production_api.Client', side_effect=AssertionError('Test mode must not call the CRM API')):
                    with self.assertRaises(HTTPError) as error:
                        get_state()
                    self.assertEqual(error.exception.code, 401)
                    with patch.object(http.app, 'start', return_value='test-run'):
                        with urlopen(Request(base+'/api/session', data=b'{"mode":"test"}',
                                             headers={'Content-Type':'application/json'})) as response:
                            session = json.load(response)['token']
                    data = get_state(session)
                    self.assertEqual(data['mode'], 'test')
                    self.assertIn('/local-test-key/accounts/', data['crm_link_base'])
                    self.assertEqual(len(data['accounts']), 1)
                    with self.assertRaises(HTTPError) as error:
                        get_state(session, 'https://untrusted.example')
                    self.assertEqual(error.exception.code, 403)
                    with urlopen(base+'/index.html') as response:
                        self.assertNotIn(b'local-test-key', response.read())
                with patch.dict(os.environ, {'BELLHAVEN_API_TOKEN': ''}):
                    data = get_state(session)
                    self.assertIsNone(data['crm_link_base'])
                    self.assertEqual(len(data['accounts']), 1)
            finally:
                http.shutdown()
                http.server_close()
                thread.join()

    def test_export_omits_runtime_credentials(self):
        path = server.ROOT / 'mistral-replica/export_cards.py'
        spec = importlib.util.spec_from_file_location('export_cards', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        snapshot = {'proposals': [], 'accounts': [{'account_id':'A'}], 'contacts': []}
        state = dict(snapshot, crm_link_base='https://example.test/private-key/accounts/', api_token='private-key')
        self.assertEqual(module.export_data(state), snapshot)

    def test_public_assets_and_export_do_not_embed_crm_tokens(self):
        ui = server.ROOT / 'mistral-replica'
        paths = list((ui/'dist').rglob('*.html')) + list((ui/'dist').rglob('*.js'))
        paths += list((ui/'reference/card-layouts').glob('*.html'))
        paths += list((server.ROOT/'exports').glob('*.html'))
        token = re.compile(rb'\bbh_[A-Za-z0-9_-]{20,}')
        for path in paths:
            with self.subTest(path=str(path.relative_to(server.ROOT))):
                self.assertIsNone(token.search(path.read_bytes()), 'Embedded CRM credential in public asset')


if __name__ == '__main__':
    unittest.main()
