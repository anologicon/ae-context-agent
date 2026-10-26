"""A changed business definition affects a metric, model and dashboard."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ae_agent.impact import queue, resolve
from ae_agent.store import Store

with tempfile.TemporaryDirectory(prefix='ae-impact-demo-') as temp:
    store = Store(Path(temp) / 'knowledge.db')
    try:
        definition, _, _ = store.source('confluence:revenue', 'Definição', 'Receita exclui cortesias.')
        lineage, _, _ = store.source('repo:lineage', 'Dependências',
            'Modelo receita depende da métrica Receita comercial.\nDashboard financeiro depende do Modelo receita.')
        definitions = [
            ('Receita comercial', 'metric', 'excludes', 'Cortesias', 'concept', definition, 'Receita exclui cortesias.'),
            ('Modelo receita', 'model', 'depends_on', 'Receita comercial', 'metric', lineage,
             'Modelo receita depende da métrica Receita comercial.'),
            ('Dashboard financeiro', 'dashboard', 'depends_on', 'Modelo receita', 'model', lineage,
             'Dashboard financeiro depende do Modelo receita.'),
        ]
        for subject, kind, relation, obj, obj_kind, source, quote in definitions:
            cid = store.propose(subject, relation, obj, source, quote, subject_kind=kind, object_kind=obj_kind)
            store.review(cid, 'validated', 'responsavel-demo')
        store.source('confluence:revenue', 'Definição', 'Receita exclui cortesias e estornos.')
        jobs = queue(store)
        print(json.dumps({'affected': [{'asset': j['label'], 'kind': j['kind'], 'reason': j['reason'],
            'evidence_path_length': len(j['path'])} for j in jobs]}, ensure_ascii=False, indent=2))
        report, _, _ = store.source('repo:validation', 'Resultado da revisão',
            'Modelo receita atualizado e reconciliado com a regra que exclui estornos.')
        job = next(j for j in jobs if j['kind'] == 'model')
        resolve(store, job['id'], 'updated', 'responsavel-demo', 'Exemplo de revisão manual', report,
                'Modelo receita atualizado e reconciliado com a regra que exclui estornos.')
        print(json.dumps({'remaining': [j['label'] for j in queue(store)]}, ensure_ascii=False, indent=2))
    finally:
        store.close()
