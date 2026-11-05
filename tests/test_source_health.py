import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, Mock
import requests
import punish_tracker as p

FIXTURES = Path(__file__).parent / 'fixtures'
TODAY = datetime(2026, 10, 3)

class SourceHealthTests(unittest.TestCase):
    def setUp(self):
        self.previous = p.load_source_state(FIXTURES / '20261002_visible.json')
        self.twse = json.loads((FIXTURES / '20261003_twse.json').read_text())
        for r in self.twse:
            for key in ('start', 'end'):
                r[key] = datetime.fromisoformat(r[key])

    def test_incident_real_visible_records(self):
        twse = p.reconcile_source(p.SourceSnapshot(self.twse, True), self.previous['TWSE'], TODAY)
        tpex = p.reconcile_source(p.SourceSnapshot([], False, 'NameResolutionError'), self.previous['TPEx'], TODAY)
        self.assertTrue(twse['healthy'])
        self.assertFalse(tpex['healthy'])
        self.assertEqual(len(twse['records']), 9)
        self.assertEqual(len(tpex['records']), 21)
        self.assertEqual(len({r['code'] for r in twse['records'] + tpex['records']}), 30)
        self.assertNotIn('7772', [r['code'] for r in tpex['records']])
        self.assertIn('4174', [r['code'] for r in tpex['records']])  # starts 10/5

    def test_dns_retry_is_failed_snapshot(self):
        with patch.object(p.requests, 'get', side_effect=requests.ConnectionError('NameResolutionError')), patch.object(p.time, 'sleep'):
            result = p.fetch_tpex_punish()
        self.assertFalse(result.healthy)
        self.assertIn('NameResolutionError', result.error)

    def test_shrink_keeps_old_and_accepts_new(self):
        new = copy.deepcopy(self.previous['TPEx']['records'][:1])
        new[0]['code'] = 'NEW'
        result = p.reconcile_source(p.SourceSnapshot(new, True), self.previous['TPEx'], TODAY)
        self.assertFalse(result['healthy'])
        self.assertEqual(len(result['records']), 22)

    def test_confirmed_empty_removes_unexpired(self):
        result = p.reconcile_source(p.SourceSnapshot([], True), self.previous['TPEx'], TODAY,
            confirm=lambda: p.SourceSnapshot([], True))
        self.assertTrue(result['healthy'])
        self.assertEqual(result['records'], [])

    def test_incomplete_confirmation_blocks_removal(self):
        result = p.reconcile_source(p.SourceSnapshot([], True), self.previous['TPEx'], TODAY,
            confirm=lambda: p.SourceSnapshot([], True, complete=False))
        self.assertFalse(result['healthy'])
        self.assertEqual(len(result['records']), 21)

    def test_healthy_snapshot_removes_missing(self):
        rows = [r for r in self.previous['TPEx']['records'] if r['end'] >= TODAY][1:]
        result = p.reconcile_source(p.SourceSnapshot(rows, True), self.previous['TPEx'], TODAY)
        self.assertTrue(result['healthy'])
        self.assertEqual(len(result['records']), 20)

    def test_degraded_main_blocks_all_publication_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch.object(p.sys, 'argv', ['punish_tracker.py', '--tg', '--deploy']), \
                 patch.object(p, 'load_source_state', return_value=self.previous), \
                 patch.object(p, 'save_source_state', side_effect=lambda state: p.save_source_state_original(state, path)), \
                 patch.object(p, 'fetch_twse_punish', return_value=p.SourceSnapshot(self.twse, True)), \
                 patch.object(p, 'fetch_tpex_punish', return_value=p.SourceSnapshot([], False, 'DNS')), \
                 patch.object(p, 'GITHUB_TOKEN', ''), \
                 patch.object(p, 'send_telegram') as tg, \
                 patch.object(p, 'send_telegram_document') as doc, \
                 patch.object(p, 'deploy_to_github') as deploy:
                self.assertEqual(p.main(), 2)
                tg.assert_not_called(); doc.assert_not_called(); deploy.assert_not_called()
            self.assertEqual(len(p.load_source_state(path)['TPEx']['records']), 21)

p.save_source_state_original = p.save_source_state
if __name__ == '__main__':
    unittest.main()
