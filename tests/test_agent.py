import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from ae_agent.cli import main
from ae_agent.discovery import LocalConnector
from ae_agent.store import Store


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / '.ae-agent' / 'knowledge.sqlite3')

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def source(self, text='Receita exclui cortesias.'):
        return self.store.source('confluence:revenue', 'Receita', text)

    def claim(self):
        sid, _, _ = self.source()
        return self.store.propose('Receita', 'excludes', 'Cortesias', sid, 'Receita exclui cortesias.')

    def test_unchanged_source_is_idempotent(self):
        first = self.source()
        second = self.source()
        self.assertEqual(first[:2], second[:2])
        self.assertFalse(second[2])
        self.assertEqual(self.store.status()['events'], 1)

    def test_change_invalidates_validated_claim_and_preserves_evidence(self):
        cid = self.claim()
        self.store.review(cid, 'validated', 'financeiro')
        self.source('Receita inclui cortesias.')
        claim = self.store.claims()[0]
        self.assertEqual(claim['status'], 'stale')
        self.assertEqual(claim['source_revision'], 1)
        self.assertEqual(claim['quote'], 'Receita exclui cortesias.')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM revisions').fetchone()[0], 2)

    def test_fabricated_evidence_refused(self):
        sid, _, _ = self.source()
        with self.assertRaises(ValueError):
            self.store.propose('Receita', 'excludes', 'Impostos', sid, 'Exclui impostos')

    def test_stale_cannot_be_revalidated_without_new_proposal(self):
        cid = self.claim()
        self.source('Outra regra')
        with self.assertRaises(ValueError):
            self.store.review(cid, 'validated', 'financeiro')

    def test_claim_idempotency_and_temporal_distinction(self):
        cid = self.claim()
        self.assertEqual(cid, self.claim())
        sid, _, _ = self.source()
        other = self.store.propose('Receita', 'excludes', 'Cortesias', sid, 'Receita exclui cortesias.', '2026-07-01')
        self.assertNotEqual(cid, other)
        self.assertEqual(len(self.store.claims()), 2)

    def test_local_deletion_marks_knowledge_stale(self):
        path = self.root / 'doc.md'
        path.write_text('[modelo](model.sql)')
        connector = LocalConnector(self.root)
        connector.scan(self.store)
        cid = self.store.claims()[0]['id']
        self.store.review(cid, 'validated', 'owner')
        path.unlink()
        result = connector.scan(self.store)
        self.assertEqual(result['removed'], 1)
        self.assertEqual(self.store.claims()[0]['status'], 'stale')

    def test_incomplete_scan_does_not_infer_removal(self):
        path = self.root / 'doc.md'
        path.write_text('valuable')
        connector = LocalConnector(self.root)
        connector.scan(self.store)
        path.unlink()
        (self.root / '.mcp.json').write_text('{invalid')
        result = connector.scan(self.store)
        self.assertTrue(result['errors'])
        self.assertEqual(result['removed'], 0)
        self.assertEqual(self.store.status()['sources'], 1)

    def test_mcp_detected_not_executed_or_secret_stored(self):
        (self.root / '.mcp.json').write_text(json.dumps({'mcpServers': {'warehouse': {'command': 'touch SHOULD_NOT_EXIST', 'env': {'TOKEN': 'super-secret-value'}}}}))
        LocalConnector(self.root).scan(self.store)
        status = self.store.status()
        self.assertEqual(status['capabilities'][0]['state'], 'detected')
        self.assertFalse((self.root / 'SHOULD_NOT_EXIST').exists())
        self.assertNotIn('super-secret-value', '\n'.join(self.store.db.iterdump()))

    def test_symlinks_and_secret_files_excluded(self):
        (self.root / '.env').write_text('secret')
        (self.root / 'credentials.json').write_text('secret')
        (self.root / 'link.md').symlink_to(self.root / '.env')
        result = LocalConnector(self.root).scan(self.store)
        self.assertEqual(result['observed'], 0)

    def test_explicit_ignore_and_redaction(self):
        (self.root / '.aeignore').write_text('private/*\n')
        (self.root / 'private').mkdir()
        (self.root / 'private' / 'doc.md').write_text('private-value')
        (self.root / 'public.md').write_text('api_key=abcdef\npostgres://user:pass@host/db')
        LocalConnector(self.root).scan(self.store)
        dump = '\n'.join(self.store.db.iterdump())
        for secret in ('private-value', 'abcdef', 'user:pass'):
            self.assertNotIn(secret, dump)
        self.assertIn('[REDACTED]', dump)

    def test_search_treats_wildcards_as_literal(self):
        self.source('Receita cresce 10%.')
        self.assertEqual(len(self.store.search('10%')), 1)
        self.assertEqual(len(self.store.search('_')), 0)
        self.assertEqual(len(self.store.search("' OR 1=1 --")), 0)

    def test_graph_has_evidence_and_scope(self):
        self.claim()
        graph = self.store.graph()
        self.assertEqual(len(graph['nodes']), 2)
        self.assertEqual(graph['edges'][0]['status'], 'proposed')
        self.assertTrue(graph['edges'][0]['source_uri'])

    def test_capability_removal_logged(self):
        path = self.root / '.mcp.json'
        path.write_text('{"mcpServers":{"catalog":{}}}')
        connector = LocalConnector(self.root)
        connector.scan(self.store)
        path.unlink()
        connector.scan(self.store)
        self.assertEqual(self.store.status()['capabilities'], [])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE type='capability.unavailable'").fetchone()[0], 1)

    def test_cli_end_to_end(self):
        (self.root / 'revenue.md').write_text('Receita exclui cortesias.')
        def run(*args):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(['--root', str(self.root), *args])
            self.assertEqual(result, 0)
            return json.loads(output.getvalue())
        run('scan')
        sid = run('search', 'Receita')[0]['id']
        cid = run('propose', '--subject', 'Receita', '--relation', 'excludes', '--object', 'Cortesias', '--source', sid, '--quote', 'Receita exclui cortesias.')['claim_id']
        run('review', cid, '--decision', 'validated', '--reviewer', 'financeiro')
        self.assertEqual(run('context', 'Receita')['claims'][0]['status'], 'validated')
        (self.root / 'revenue.md').write_text('Receita inclui cortesias.')
        run('watch', '--cycles', '1')
        self.assertEqual(run('claims')[0]['status'], 'stale')


if __name__ == '__main__':
    unittest.main()
