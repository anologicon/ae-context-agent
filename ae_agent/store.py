"""Versioned sources and claims. Retrieval indexes are never the source of truth."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .impact import NODE_KINDS, invalidate, migrate, queue


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        if self.db.execute("PRAGMA user_version").fetchone()[0] > 2:
            self.db.close()
            raise ValueError("Banco criado por uma versão mais recente do agente.")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS sources (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, uri TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL, scope TEXT NOT NULL, revision INTEGER NOT NULL,
          hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS revisions (
          source_id TEXT NOT NULL REFERENCES sources(id), revision INTEGER NOT NULL,
          content TEXT NOT NULL, hash TEXT NOT NULL, observed_at TEXT NOT NULL,
          PRIMARY KEY(source_id, revision));
        CREATE TABLE IF NOT EXISTS nodes (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL, scope TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS claims (
          id TEXT PRIMARY KEY, subject TEXT NOT NULL REFERENCES nodes(id),
          relation TEXT NOT NULL, object TEXT NOT NULL REFERENCES nodes(id),
          source_id TEXT NOT NULL, source_revision INTEGER NOT NULL,
          quote TEXT NOT NULL, status TEXT NOT NULL, valid_from TEXT,
          reviewer TEXT, created_at TEXT NOT NULL,
          FOREIGN KEY(source_id, source_revision) REFERENCES revisions(source_id, revision));
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, type TEXT NOT NULL, payload TEXT NOT NULL, at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS capabilities (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
          origin TEXT NOT NULL, state TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        """)
        migrate(self.db)

    def close(self):
        self.db.close()

    def event(self, kind, payload):
        return self.db.execute("INSERT INTO events(type,payload,at) VALUES (?,?,?)",
                        (kind, json.dumps(payload, ensure_ascii=False), now())).lastrowid

    def source(self, uri, title, content, kind="document", scope="project"):
        sid, fingerprint = digest(uri)[:24], digest(content)
        old = self.db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
        if old and old["hash"] == fingerprint and old["active"]:
            return sid, old["revision"], False
        revision = old["revision"] + 1 if old else 1
        with self.db:
            self.db.execute("""INSERT INTO sources VALUES (?,?,?,?,?,?,?,1)
              ON CONFLICT(id) DO UPDATE SET title=excluded.title, revision=excluded.revision,
              hash=excluded.hash,active=1""", (sid, kind, uri, title, scope, revision, fingerprint))
            self.db.execute("INSERT INTO revisions VALUES (?,?,?,?,?)",
                            (sid, revision, content, fingerprint, now()))
            event_id = self.event("source.changed" if old else "source.discovered", {"source_id": sid, "revision": revision})
            if old:
                invalidate(self, sid, event_id)
        return sid, revision, True

    def deactivate_missing(self, present):
        missing = [r["id"] for r in self.db.execute("SELECT id FROM sources WHERE active=1 AND kind='local'") if r["id"] not in present]
        with self.db:
            for sid in missing:
                self.db.execute("UPDATE sources SET active=0 WHERE id=?", (sid,))
                event_id = self.event("source.removed", {"source_id": sid})
                invalidate(self, sid, event_id)
        return len(missing)

    def node(self, kind, label, scope):
        if kind not in NODE_KINDS:
            raise ValueError("Tipo de nó desconhecido.")
        nid = digest(json.dumps([kind, label, scope]))[:24]
        self.db.execute("INSERT OR IGNORE INTO nodes VALUES (?,?,?,?)", (nid, kind, label, scope))
        return nid

    def propose(self, subject, relation, obj, source_id, quote, valid_from=None,
                subject_kind="concept", object_kind="concept"):
        source = self.db.execute("SELECT * FROM sources WHERE id=? AND active=1", (source_id,)).fetchone()
        if not source:
            raise ValueError("Fonte ativa não encontrada.")
        content = self.db.execute("SELECT content FROM revisions WHERE source_id=? AND revision=?", (source_id, source["revision"])).fetchone()[0]
        if not quote.strip() or quote not in content:
            raise ValueError("A evidência deve ser um trecho literal da revisão atual da fonte.")
        if not all(x.strip() for x in (subject, relation, obj)):
            raise ValueError("Sujeito, relação e objeto não podem estar vazios.")
        if valid_from:
            from datetime import date
            date.fromisoformat(valid_from)
        identity = [subject, relation, obj, source_id, source["revision"], quote, valid_from]
        # Preserve ids of existing untyped claims after migration.
        if (subject_kind, object_kind) != ("concept", "concept"):
            identity.extend([subject_kind, object_kind])
        cid = digest(json.dumps(identity))[:24]
        with self.db:
            a = self.node(subject_kind, subject, source["scope"])
            b = self.node(object_kind, obj, source["scope"])
            result = self.db.execute("INSERT OR IGNORE INTO claims VALUES (?,?,?,?,?,?,?,'proposed',?,NULL,?)",
                                    (cid, a, relation, b, source_id, source["revision"], quote, valid_from, now()))
            if result.rowcount:
                self.event("claim.proposed", {"claim_id": cid})
        return cid

    def review(self, cid, decision, reviewer):
        if decision not in ("validated", "rejected") or not reviewer.strip():
            raise ValueError("Decisão ou responsável inválido.")
        row = self.db.execute("""SELECT c.*, s.active, s.revision FROM claims c
            JOIN sources s ON c.source_id=s.id WHERE c.id=?""", (cid,)).fetchone()
        if not row or row["status"] != "proposed" or not row["active"] or row["source_revision"] != row["revision"]:
            raise ValueError("Só propostas apoiadas na revisão atual podem ser revisadas.")
        with self.db:
            self.db.execute("UPDATE claims SET status=?,reviewer=? WHERE id=?", (decision, reviewer, cid))
            self.event("claim.reviewed", {"claim_id": cid, "decision": decision, "reviewer": reviewer})

    def search(self, query):
        # Parameterized literal substring search; no LLM or vector service required.
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return [dict(r) for r in self.db.execute("""SELECT s.*, substr(r.content,1,1200) AS preview
          FROM sources s JOIN revisions r ON r.source_id=s.id AND r.revision=s.revision
          WHERE s.active=1 AND (s.title LIKE ? ESCAPE '\\' OR r.content LIKE ? ESCAPE '\\')
          ORDER BY s.title LIMIT 30""", (pattern, pattern))]

    def claims(self):
        return [dict(r) for r in self.db.execute("""SELECT c.*,a.label AS subject_label,b.label AS object_label,
            a.kind AS subject_kind,b.kind AS object_kind,
            s.uri AS source_uri FROM claims c JOIN nodes a ON a.id=c.subject JOIN nodes b ON b.id=c.object
            JOIN sources s ON s.id=c.source_id ORDER BY c.created_at,c.id""")]

    def graph(self):
        return {"nodes": [dict(r) for r in self.db.execute("SELECT * FROM nodes ORDER BY id")],
                "edges": self.claims(), "sources": [dict(r) for r in self.db.execute("SELECT * FROM sources ORDER BY id")],
                "revalidations": queue(self)}

    def inventory(self, capabilities):
        with self.db:
            previous = {r["id"] for r in self.db.execute("SELECT id FROM capabilities WHERE active=1")}
            self.db.execute("UPDATE capabilities SET active=0")
            current = set()
            for cap in capabilities:
                cid = digest(json.dumps(cap, sort_keys=True))[:24]
                current.add(cid)
                self.db.execute("INSERT INTO capabilities VALUES (?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET active=1",
                                (cid, cap["kind"], cap["name"], cap["origin"], "detected"))
                if cid not in previous:
                    self.event("capability.detected", {"id": cid, **cap})
            for cid in previous - current:
                self.event("capability.unavailable", {"id": cid})
        return {"added": len(current - previous), "removed": len(previous - current)}

    def status(self):
        return {"sources": self.db.execute("SELECT count(*) FROM sources WHERE active=1").fetchone()[0],
                "claims": {r[0]: r[1] for r in self.db.execute("SELECT status,count(*) FROM claims GROUP BY status")},
                "capabilities": [dict(r) for r in self.db.execute("SELECT * FROM capabilities WHERE active=1")],
                "events": self.db.execute("SELECT count(*) FROM events").fetchone()[0],
                "pending_revalidations": self.db.execute("SELECT count(*) FROM revalidations WHERE status='pending'").fetchone()[0],
                "schema_version": self.db.execute("PRAGMA user_version").fetchone()[0]}
