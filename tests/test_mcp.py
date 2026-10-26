import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ae_agent.mcp import Server
from ae_agent.store import Store


class MCPTest(unittest.TestCase):
    def test_stdio_handshake_and_tool_call(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / '.ae-agent' / 'knowledge.sqlite3')
            store.close()
            requests = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'environment_status', 'arguments': {}}},
            ]
            result = subprocess.run([sys.executable, '-m', 'ae_agent', '--root', temp, 'serve'],
                input='\n'.join(json.dumps(r) for r in requests) + '\n', text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            responses = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual([r['id'] for r in responses], [1, 2, 3])
            self.assertEqual(len(responses[1]['result']['tools']), 2)
            self.assertFalse(responses[2]['result']['isError'])

    def test_rejects_unknown_or_malformed_tools_and_fabricated_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'db')
            self.addCleanup(store.close)
            server = Server(store, Path(temp), allow_content=True)
            server.ready = True
            def call(name, args):
                return server.handle({'jsonrpc':'2.0', 'id':1, 'method':'tools/call', 'params':{'name':name, 'arguments':args}})
            self.assertIn('error', call('review_claim', {}))
            self.assertIn('error', call('search_sources', {'query': 1}))
            sid, _, _ = store.source('drive:1', 'Test', 'Revenue')
            result = call('propose_claim', {'subject':'Revenue', 'relation':'excludes', 'object':'Taxes', 'source_id':sid, 'quote':'invented'})
            self.assertTrue(result['result']['isError'])
