import json
import os
from pathlib import Path
import subprocess
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process = subprocess.Popen([os.sys.executable, '-m', 'savethesnap', 'ui', '--demo', '--no-browser'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        cls.process.stdout.readline()
        url = cls.process.stdout.readline().strip()
        parts = urlsplit(url)
        cls.origin = f'{parts.scheme}://{parts.netloc}'
        cls.token = parts.fragment
        if not cls.token:
            cls.process.terminate()
            raise RuntimeError('Synthetic fixture server did not start.')

    @classmethod
    def tearDownClass(cls):
        try:
            cls.call('stop', {})
            cls.process.wait(timeout=10)
        finally:
            if cls.process.poll() is None:
                cls.process.terminate()
                cls.process.wait(timeout=10)
            cls.process.stdout.close()
            cls.process.stderr.close()

    @classmethod
    def call(cls, endpoint, data=None, token=None, origin=None):
        headers = {'X-SaveTheSnap': cls.token if token is None else token}
        if origin:
            headers['Origin'] = origin
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(cls.origin + '/api/' + endpoint,
            data=json.dumps(data).encode() if data is not None else None, headers=headers)
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    def settle(self):
        for _ in range(100):
            state = self.call('state')
            if not state['busy']:
                return state
            time.sleep(.05)
        self.fail('Synthetic operation did not settle.')

    def test_01_unknown_token_and_external_origins_cannot_read_or_write(self):
        for kwargs in [{'token': 'incorrect'}, {'origin': 'https://unrelated.invalid'}]:
            with self.assertRaises(HTTPError) as caught:
                self.call('state', **kwargs)
            self.assertEqual(caught.exception.code, 403)
            caught.exception.close()
        with self.assertRaises(HTTPError) as caught:
            self.call('scan', {'source': 'anything'}, origin='https://unrelated.invalid')
        self.assertEqual(caught.exception.code, 403)
        caught.exception.close()

    def test_02_prepare_is_bound_to_the_scanned_source_and_scan_id(self):
        state = self.call('state')
        for changes in [{'scan_id': 'old'}, {'source': state['source'] + '-wrong'}]:
            body = {'source': state['source'], 'scan_id': state['scan_id'], 'output': state['output'], 'limit': 1}
            body.update(changes)
            with self.assertRaises(HTTPError) as caught:
                self.call('prepare', body)
            caught.exception.close()
        self.assertFalse(self.call('state')['busy'])

    @unittest.skipUnless(__import__('shutil').which('ffmpeg'), 'FFmpeg not installed')
    def test_03_next_batch_does_not_repeat_the_first_items(self):
        ids = []
        for expected in (1, 2):
            state = self.call('state')
            self.call('prepare', {'source': state['source'], 'scan_id': state['scan_id'],
                'output': state['output'], 'limit': 1})
            state = self.settle()
            self.assertEqual(state['job']['status'], 'done')
            self.assertEqual(state['prepared'], expected)
            report = json.loads((Path(state['last_run']['folder']) / 'manifest.json').read_text())
            ids.extend(r['id'] for r in report['results'])
        self.assertEqual(len(ids), len(set(ids)))

    def test_04_failed_rescan_clears_stale_inventory_and_review(self):
        self.call('scan', {'source': 'this-synthetic-source-does-not-exist'})
        state = self.settle()
        self.assertEqual(state['job']['status'], 'error')
        self.assertIsNone(state['summary'])
        self.assertIsNone(state['last_run'])
        self.assertIsNone(state['scan_id'])
        with self.assertRaises(HTTPError) as caught:
            self.call('prepare', {'source': state['source'], 'scan_id': state['scan_id'], 'limit': 1})
        caught.exception.close()


if __name__ == '__main__':
    unittest.main()
