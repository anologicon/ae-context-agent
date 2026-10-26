"""Conservative impact traversal over explicitly reviewed dependencies."""
import json
from collections import deque

NODE_KINDS = ("concept", "metric", "model", "table", "dashboard", "document", "repository", "team")


def migrate(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > 2:
        raise ValueError("Banco criado por uma versão mais recente do agente.")
    if version < 2:
        db.executescript("""
        BEGIN;
        CREATE TABLE IF NOT EXISTS revalidations (
          id INTEGER PRIMARY KEY,
          event_id INTEGER NOT NULL REFERENCES events(id),
          source_id TEXT NOT NULL REFERENCES sources(id),
          node_id TEXT NOT NULL REFERENCES nodes(id),
          reason TEXT NOT NULL, path TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          resolution TEXT, reviewer TEXT, note TEXT,
          evidence_source TEXT, evidence_revision INTEGER, evidence_quote TEXT,
          resolved_at TEXT,
          UNIQUE(event_id,node_id),
          FOREIGN KEY(evidence_source,evidence_revision) REFERENCES revisions(source_id,revision));
        CREATE INDEX IF NOT EXISTS claims_object_idx ON claims(object,relation,status);
        CREATE INDEX IF NOT EXISTS revalidation_status_idx ON revalidations(status);
        PRAGMA user_version=2;
        COMMIT;
        """)


def invalidate(store, source_id, event_id):
    """Called inside source mutation's transaction. No model inference involved.

    Stale-but-previously-reviewed dependencies remain candidates conservatively.
    Their continued truth must be checked by the queue consumer.
    """
    db = store.db
    roots = db.execute("""SELECT * FROM claims WHERE source_id=?
        AND status IN ('proposed','validated','stale') ORDER BY source_revision DESC,id""", (source_id,)).fetchall()
    db.execute("UPDATE claims SET status='stale' WHERE source_id=? AND status IN ('proposed','validated')", (source_id,))
    db.execute("UPDATE revalidations SET status='superseded' WHERE source_id=? AND status='pending'", (source_id,))
    paths = {}
    for root in roots:
        paths.setdefault(root['subject'], [root['id']])
    adjacency = {}
    for edge in db.execute("""SELECT c.id,c.subject,c.object FROM claims c
          JOIN nodes a ON a.id=c.subject JOIN nodes b ON b.id=c.object
          WHERE c.relation='depends_on' AND c.status IN ('validated','stale')
          AND c.reviewer IS NOT NULL AND a.scope=b.scope ORDER BY c.id"""):
        adjacency.setdefault(edge['object'], []).append((edge['subject'], edge['id']))
    direct = set(paths)
    pending = deque(paths)
    while pending:
        node = pending.popleft()
        for dependent, edge_id in adjacency.get(node, []):
            if dependent not in paths:
                paths[dependent] = paths[node] + [edge_id]
                pending.append(dependent)
    for node, path in paths.items():
        db.execute("""INSERT INTO revalidations(event_id,source_id,node_id,reason,path)
            VALUES (?,?,?,?,?)""", (event_id, source_id, node,
                'source_evidence_changed' if node in direct else 'dependency_affected', json.dumps(path)))
    return len(paths)


def queue(store, include_closed=False):
    rows = store.db.execute("""SELECT r.*,n.label,n.kind,n.scope,s.uri AS trigger_source_uri
        FROM revalidations r JOIN nodes n ON n.id=r.node_id JOIN sources s ON s.id=r.source_id
        WHERE (? OR r.status='pending') ORDER BY r.id""", (int(include_closed),))
    result = []
    for row in rows:
        value = dict(row)
        value['path'] = json.loads(value['path'])
        result.append(value)
    return result


def resolve(store, job_id, outcome, reviewer, note, evidence_source, quote):
    if outcome not in ('unchanged', 'updated', 'dismissed') or not reviewer.strip() or not note.strip():
        raise ValueError("Informe resultado, responsável e justificativa.")
    source = store.db.execute("SELECT * FROM sources WHERE id=? AND active=1", (evidence_source,)).fetchone()
    job = store.db.execute("SELECT * FROM revalidations WHERE id=? AND status='pending'", (job_id,)).fetchone()
    if not source or not job:
        raise ValueError("Revalidação pendente ou fonte ativa não encontrada.")
    node = store.db.execute("SELECT scope FROM nodes WHERE id=?", (job['node_id'],)).fetchone()
    if source['scope'] != node['scope']:
        raise ValueError("Evidência de outro escopo não pode encerrar esta revalidação.")
    content = store.db.execute("SELECT content FROM revisions WHERE source_id=? AND revision=?",
                              (evidence_source, source['revision'])).fetchone()[0]
    if not quote.strip() or quote not in content:
        raise ValueError("A evidência deve existir na revisão atual.")
    from .store import now
    with store.db:
        store.db.execute("""UPDATE revalidations SET status='resolved',resolution=?,reviewer=?,note=?,
            evidence_source=?,evidence_revision=?,evidence_quote=?,resolved_at=? WHERE id=?""",
            (outcome, reviewer, note, evidence_source, source['revision'], quote, now(), job_id))
        store.event('revalidation.resolved', {'id': job_id, 'outcome': outcome, 'reviewer': reviewer})
