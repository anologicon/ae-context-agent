"""Demonstrate learning and invalidation without changing the bundled fixture."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ae_agent.discovery import LocalConnector
from ae_agent.store import Store

with tempfile.TemporaryDirectory(prefix='ae-agent-demo-') as temp:
    root = Path(temp)
    document = root / 'revenue.md'
    document.write_text('Receita comercial exclui cortesias.', encoding='utf-8')
    store = Store(root / '.ae-agent' / 'knowledge.sqlite3')
    try:
        connector = LocalConnector(root)
        connector.scan(store)
        source = store.search('Receita')[0]
        claim = store.propose('Receita comercial', 'excludes', 'Cortesias', source['id'],
                              'Receita comercial exclui cortesias.', '2026-07-01')
        store.review(claim, 'validated', 'financeiro-demo')
        before = store.claims()[0]['status']
        document.write_text('Receita comercial exclui cortesias e estornos.', encoding='utf-8')
        connector.scan(store)
        after = store.claims()[0]
        print(json.dumps({'before': before, 'after': after['status'],
            'preserved_evidence': after['quote'], 'evidence_revision': after['source_revision'],
            'current_revision': store.search('Receita')[0]['revision']}, ensure_ascii=False, indent=2))
    finally:
        store.close()
