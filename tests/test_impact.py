import sqlite3
import tempfile
import unittest
from pathlib import Path

from ae_agent.impact import queue, resolve
from ae_agent.store import Store


class ImpactTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'knowledge.db'
        self.store = Store(self.path)
        self.source_id, _, _ = self.store.source('drive:revenue', 'Revenue', 'Revenue excludes gifts')
        self.dependencies, _, _ = self.store.source('repo:lineage', 'Lineage', 'model -> revenue; dashboard -> model; other -> revenue')
        self.rule = self.store.propose('Revenue', 'excludes', 'Gifts', self.source_id,
                                      'Revenue excludes gifts', subject_kind='metric')
        self.store.review(self.rule, 'validated', 'finance')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def dependency(self, name, kind, target, target_kind, validated=True):
        cid = self.store.propose(name, 'depends_on', target, self.dependencies,
                                'model -> revenue', subject_kind=kind, object_kind=target_kind)
        if validated:
            self.store.review(cid, 'validated', 'data-team')
        return cid

    def change(self, text='Revenue excludes gifts and refunds'):
        self.store.source('drive:revenue', 'Revenue', text)

    def test_impact_propagates_over_reviewed_edges_only(self):
        edge = self.dependency('Revenue model', 'model', 'Revenue', 'metric')
        self.dependency('Finance dashboard', 'dashboard', 'Revenue model', 'model')
        self.dependency('Unreviewed', 'model', 'Revenue', 'metric', False)
        self.change()
        jobs = {j['label']: j for j in queue(self.store)}
        self.assertEqual(set(jobs), {'Revenue', 'Revenue model', 'Finance dashboard'})
        self.assertEqual(len(jobs['Finance dashboard']['path']), 3)
        self.assertIn(edge, jobs['Finance dashboard']['path'])
        # Downstream evidence is still current: queue impact without asserting it false.
        self.assertEqual(next(c for c in self.store.claims() if c['id'] == edge)['status'], 'validated')

    def test_cycle_terminates_and_deduplicates(self):
        self.dependency('Model', 'model', 'Revenue', 'metric')
        self.dependency('Revenue', 'metric', 'Model', 'model')
        self.change()
        self.assertEqual(len(queue(self.store)), 2)

    def test_same_label_different_kind_does_not_merge(self):
        self.dependency('Wrong domain object', 'dashboard', 'Revenue', 'table')
        self.change()
        self.assertEqual([j['label'] for j in queue(self.store)], ['Revenue'])

    def test_later_change_supersedes_pending_jobs_and_uses_stale_roots(self):
        self.dependency('Model', 'model', 'Revenue', 'metric')
        self.change()
        old = queue(self.store)[0]['id']
        self.change('New revenue definition')
        self.assertEqual(len(queue(self.store)), 2)
        self.assertEqual(len([j for j in queue(self.store, True) if j['status'] == 'superseded']), 2)
        with self.assertRaises(ValueError):
            resolve(self.store, old, 'unchanged', 'owner', 'checked', self.source_id, 'New revenue definition')

    def test_resolution_requires_current_evidence_and_preserves_stale_rule(self):
        self.change()
        job = queue(self.store)[0]
        with self.assertRaises(ValueError):
            resolve(self.store, job['id'], 'updated', 'owner', 'done', self.source_id, 'fabricated')
        resolve(self.store, job['id'], 'updated', 'owner', 'Definition reviewed', self.source_id,
                'Revenue excludes gifts and refunds')
        self.assertEqual(queue(self.store), [])
        self.assertEqual(queue(self.store, True)[0]['evidence_revision'], 2)
        self.assertEqual(self.store.claims()[0]['status'], 'stale')

    def test_scope_prevents_cross_domain_dependency(self):
        sid, _, _ = self.store.source('drive:other', 'Other', 'Revenue drives dashboard', scope='different')
        cid = self.store.propose('Other dashboard', 'depends_on', 'Revenue', sid,
                                 'Revenue drives dashboard', subject_kind='dashboard', object_kind='metric')
        self.store.review(cid, 'validated', 'other-owner')
        self.change()
        self.assertEqual(len(queue(self.store)), 1)

    def test_schema_migration_preserves_existing_claims(self):
        self.store.close()
        db = sqlite3.connect(self.path)
        db.execute('DROP TABLE revalidations')
        db.execute('PRAGMA user_version=0')
        db.commit()
        db.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.status()['schema_version'], 2)
        self.assertEqual(self.store.claims()[0]['id'], self.rule)
        self.change()
        self.assertEqual(len(queue(self.store)), 1)

    def test_future_schema_rejected(self):
        future = Path(self.temp.name) / 'future.db'
        db = sqlite3.connect(future)
        db.execute('PRAGMA user_version=99')
        db.close()
        with self.assertRaises(ValueError):
            Store(future)
