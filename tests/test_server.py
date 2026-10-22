import base64
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import ai
from server import make_handler
from store import Store
from llama_runtime import LlamaConfig
try:
    from tests.test_memory import SAMPLE
except ModuleNotFoundError:
    from test_memory import SAMPLE


class FrontendContractTests(unittest.TestCase):
    def test_scope_label_distinguishes_retrieved_from_final_cited_evidence(self):
        script = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('scope.evidence_messages', script)
        self.assertIn('scope.final_evidence_messages', script)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.sqlite3")
        self.runtime_calls = []
        test = self

        class FakeRuntime:
            def start(self, config):
                test.runtime_calls.append(('start', config))
                return {'state': 'loading', 'owner_token': 'owned-token', 'owned': True, 'can_stop': True}

            def status(self):
                test.runtime_calls.append(('status', None))
                return {'state': 'available', 'owned': True, 'can_stop': True}

            def stop(self, token):
                test.runtime_calls.append(('stop', token))
                return {'state': 'stopped', 'owned': False, 'can_stop': False, 'stopped': token == 'owned-token'}

        self.config_path = Path(self.temp.name) / 'runtime' / 'llama.json'
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(self.store, runtime=FakeRuntime(), config_path=self.config_path),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def post(self, path, data, **headers):
        request = Request(self.base + path, data=json.dumps(data).encode(), headers={
            "Content-Type": "application/json", "X-Memory-Client": "1", **headers
        })
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def get(self, path):
        with urlopen(self.base + path, timeout=5) as response:
            return json.load(response)

    def error_json(self, error):
        return json.loads(error.read().decode('utf-8'))

    def test_preview_import_search_context_and_export(self):
        upload = {"filename": "sample.txt", "base64": base64.b64encode(SAMPLE.encode()).decode(), "name": "测试"}
        preview = self.post("/api/preview", upload)
        self.assertEqual(preview["count"], 6)
        record = self.post("/api/import", upload)
        found = self.post("/api/search", {"contact_id": record["contact_id"], "query": "喝茶"})
        self.assertTrue(found["results"])
        message = found["results"][0]
        with urlopen(self.base + "/api/context?id=" + str(message["id"])) as response:
            context = json.load(response)
        self.assertEqual(context["focus"]["id"], message["id"])
        with urlopen(self.base + "/api/source?id=" + str(record["source_id"])) as response:
            self.assertEqual(response.read(), SAMPLE.encode())
        with urlopen(self.base + "/api/export?id=" + str(record["contact_id"])) as response:
            self.assertIn("attachment", response.headers["Content-Disposition"])
            self.assertIn("咖啡", response.read().decode())
        duplicate = self.post("/api/import", {**upload, "contact_id": record["contact_id"]})
        self.assertEqual(duplicate["added"], 0)

    def test_qa_returns_deterministic_and_evidence_contracts(self):
        upload = {"filename": "sample.txt", "base64": base64.b64encode(SAMPLE.encode()).decode(), "name": "问答"}
        record = self.post('/api/import', upload)
        first = self.post('/api/qa', {
            'contact_id': record['contact_id'], 'question': '我们第一次对话是哪一天？', 'filters': {},
        })
        self.assertEqual('deterministic', first['answer_kind'])
        self.assertTrue(first['answer_units'])
        related = self.post('/api/qa', {
            'contact_id': record['contact_id'], 'question': '找找喜欢饮料的相关内容', 'filters': {},
        })
        self.assertEqual('evidence', related['answer_kind'])
        self.assertIn('next_context', related)

    def test_qa_stale_context_after_import_is_not_inherited(self):
        upload = {"filename": "sample.txt", "base64": base64.b64encode(SAMPLE.encode()).decode(), "name": "上下文"}
        record = self.post('/api/import', upload)
        first = self.post('/api/qa', {
            'contact_id': record['contact_id'], 'question': '找找咖啡相关内容', 'filters': {},
        })
        extra = '[2026-12-01 10:00:00] 小林: 新增记录'
        self.post('/api/import', {
            'filename': 'extra.txt', 'base64': base64.b64encode(extra.encode()).decode(),
            'contact_id': record['contact_id'],
        })
        follow = self.post('/api/qa', {
            'contact_id': record['contact_id'], 'question': '后来呢？', 'filters': {},
            'previous': first['next_context'],
        })
        self.assertEqual('stale', follow['context_status'])

    def test_qa_stale_context_after_exact_reimport_is_not_inherited(self):
        upload = {
            "filename": "reimport.txt",
            "base64": base64.b64encode(SAMPLE.encode()).decode(),
            "name": "重复导入测试",
        }
        record = self.post('/api/import', upload)
        first = self.post('/api/qa', {
            'contact_id': record['contact_id'], 'question': '找找咖啡相关内容', 'filters': {},
        })
        duplicate = self.post('/api/import', {**upload, 'contact_id': record['contact_id']})
        self.assertTrue(duplicate['duplicate_file'])

        follow = self.post('/api/qa', {
            'contact_id': record['contact_id'], 'question': '后来呢？', 'filters': {},
            'previous': first['next_context'],
        })

        self.assertEqual('stale', follow['context_status'])
        self.assertEqual([], follow['route']['inherited_fields'])

    def test_qa_stale_context_after_trash_and_restore_is_not_inherited(self):
        upload = {
            "filename": "lifecycle.txt",
            "base64": base64.b64encode(SAMPLE.encode()).decode(),
            "name": "生命周期测试",
        }
        record = self.post('/api/import', upload)
        first = self.post('/api/qa', {
            'contact_id': record['contact_id'], 'question': '找找咖啡相关内容', 'filters': {},
        })
        self.post('/api/contact/trash', {'contact_id': record['contact_id']})
        self.post('/api/contact/restore', {'contact_id': record['contact_id']})

        follow = self.post('/api/qa', {
            'contact_id': record['contact_id'], 'question': '后来呢？', 'filters': {},
            'previous': first['next_context'],
        })

        self.assertEqual('stale', follow['context_status'])
        self.assertEqual([], follow['route']['inherited_fields'])

    def test_llama_config_start_status_and_owner_token_stop_routes(self):
        root = Path(self.temp.name)
        exe = root / 'llama-server.exe'
        model = root / 'model.gguf'
        exe.write_bytes(b'exe')
        model.write_bytes(b'model')
        config = {
            'exe_path': str(exe), 'model_path': str(model), 'port': 18081,
            'context_size': 4096, 'gpu_layers': 10, 'threads': 4,
        }
        saved = self.post('/api/llama/config', config)
        self.assertTrue(saved['configured'])
        read = self.get('/api/llama/config')
        self.assertEqual(set(config), set(read['config']))
        self.assertNotIn('owner_token', json.dumps(read))
        started = self.post('/api/llama/start', {})
        self.assertEqual('owned-token', started['owner_token'])
        self.assertEqual('available', self.get('/api/llama/status')['state'])
        stopped = self.post('/api/llama/stop', {'owner_token': 'owned-token'})
        self.assertTrue(stopped['stopped'])

    def test_cross_origin_write_rejected(self):
        with self.assertRaises(HTTPError) as error:
            self.post("/api/preview", {}, Origin="https://unrelated.invalid")
        self.assertEqual(error.exception.code, 403)

    def test_create_empty_contact_then_import_without_mixing(self):
        first = self.post('/api/contacts', {'name': ' 虚构测试档案 '})['contact_id']
        second = self.post('/api/contacts', {'name': '真实联系人测试'})['contact_id']
        self.assertNotEqual(first, second)
        contacts = {c['id']: c for c in self.store.contacts()}
        self.assertEqual(contacts[first]['name'], '虚构测试档案')
        self.assertEqual(contacts[second]['message_count'], 0)
        self.post('/api/import', {'filename': 'sample.txt', 'base64': base64.b64encode(SAMPLE.encode()).decode(), 'contact_id': second})
        self.assertEqual(len(self.store.messages(first)), 0)
        self.assertEqual(len(self.store.messages(second)), 6)

    def test_create_contact_rejects_invalid_name(self):
        for name in ['', '   ', 'a' * 101, None, 123]:
            with self.assertRaises(HTTPError) as error:
                self.post('/api/contacts', {'name': name})
            self.assertEqual(error.exception.code, 400)

    def test_bad_host_rejected(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.base + "/api/contacts", headers={"Host": "evil.invalid"}))
        self.assertEqual(error.exception.code, 403)

    def test_invalid_payload_and_path(self):
        with self.assertRaises(HTTPError) as error:
            self.post("/api/preview", {"filename": "test.txt", "base64": "not-valid!"})
        self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError):
            urlopen(self.base + "/../server.py")

    def test_ai_requires_explicit_send(self):
        record = self.post("/api/import", {"filename": "a.txt", "base64": base64.b64encode(SAMPLE.encode()).decode(), "name": "AI"})
        with self.assertRaises(HTTPError) as error:
            self.post("/api/answer", {"contact_id": record["contact_id"], "query": "茶", "evidence_ids": [1]})
        self.assertEqual(error.exception.code, 400)

    def test_trash_list_restore_and_stale_url_protection(self):
        upload = {"filename": "trash-route.txt", "base64": base64.b64encode(SAMPLE.encode()).decode(), "name": "路由回收测试"}
        record = self.post('/api/import', upload)
        message_id = self.store.messages(record['contact_id'])[0]['id']
        trashed = self.post('/api/contact/trash', {'contact_id': record['contact_id']})
        self.assertEqual(trashed['message_count'], 6)
        self.assertIn(record['contact_id'], {c['id'] for c in self.get('/api/trash')['contacts']})
        for path in (
            f'/api/context?id={message_id}',
            f'/api/source?id={record["source_id"]}',
            f'/api/export?id={record["contact_id"]}',
        ):
            with self.assertRaises(HTTPError) as error:
                self.get(path)
            self.assertEqual(error.exception.code, 400)
        for path, body in (
            ('/api/search', {'contact_id': record['contact_id'], 'query': '茶'}),
            ('/api/answer', {'contact_id': record['contact_id'], 'query': '茶', 'evidence_ids': [message_id], 'allow_send': True,
                             'config': {'endpoint': 'http://127.0.0.1:1/v1', 'model': 'test'}}),
        ):
            with self.assertRaises(HTTPError) as error:
                self.post(path, body)
            self.assertEqual(error.exception.code, 400)
            self.assertIn('联系人', self.error_json(error.exception)['error'])
        restored = self.post('/api/contact/restore', {'contact_id': record['contact_id']})
        self.assertEqual(restored['contact_id'], record['contact_id'])
        self.assertEqual(self.get(f'/api/context?id={message_id}')['focus']['id'], message_id)

    def test_exact_name_purge_route(self):
        contact_id = self.post('/api/contacts', {'name': '永久删除路由测试'})['contact_id']
        self.post('/api/contact/trash', {'contact_id': contact_id})
        with self.assertRaises(HTTPError) as error:
            self.post('/api/contact/purge', {'contact_id': contact_id, 'exact_name': '错误名称'})
        self.assertEqual(error.exception.code, 400)
        result = self.post('/api/contact/purge', {'contact_id': contact_id, 'exact_name': '永久删除路由测试'})
        self.assertEqual(result['contact_id'], contact_id)
        self.assertNotIn(contact_id, {c['id'] for c in self.get('/api/trash')['contacts']})

    def test_preview_reports_trash_matches(self):
        upload = {"filename": "trash-conflict.txt", "base64": base64.b64encode(SAMPLE.encode()).decode(), "name": "预览冲突"}
        record = self.post('/api/import', upload)
        self.post('/api/contact/trash', {'contact_id': record['contact_id']})
        preview = self.post('/api/preview', upload)
        matches = {row['id']: row for row in preview['trash_matches']}
        self.assertIn(record['contact_id'], matches)
        self.assertIn('file', matches[record['contact_id']]['reasons'])
        self.assertNotIn('raw', matches[record['contact_id']])
        self.assertNotIn('content', matches[record['contact_id']])
        with self.assertRaises(HTTPError) as error:
            self.post('/api/import', upload)
        self.assertEqual(error.exception.code, 400)
        self.assertIn('回收站', self.error_json(error.exception)['error'])
        created = self.post('/api/import', {**upload, 'trash_conflict_action': 'new'})
        self.assertNotEqual(created['contact_id'], record['contact_id'])

    def test_topics_api_and_empty_contact(self):
        text = '''联系人: 主题测试
[2026-09-01 10:00:00] 主题测试: 和妈妈坐地铁去医院喝咖啡
[2026-09-02 10:00:00] 我: 蓝色的云
'''
        record = self.post('/api/import', {"filename": "topics.txt", "base64": base64.b64encode(text.encode()).decode(), "name": "主题接口测试"})
        overview = self.get(f'/api/topics?id={record["contact_id"]}')
        self.assertEqual(overview['rule_version'], '1')
        self.assertEqual(overview['classified_messages'], 1)
        self.assertEqual([topic['name'] for topic in overview['topics']], ['饮食', '健康', '家庭朋友', '住处出行'])
        self.assertEqual(overview['topics'][0]['messages'][0]['source_name'], 'topics.txt')
        empty_id = self.post('/api/contacts', {'name': '空主题测试'})['contact_id']
        self.assertEqual(self.get(f'/api/topics?id={empty_id}')['topics'], [])
        with self.assertRaises(HTTPError) as error:
            self.get('/api/topics?id=99999999')
        self.assertEqual(error.exception.code, 400)

    def test_topics_rejects_trashed_contact(self):
        record = self.post('/api/import', {"filename": "topics-trash.txt", "base64": base64.b64encode(SAMPLE.encode()).decode(), "name": "回收主题测试"})
        self.post('/api/contact/trash', {'contact_id': record['contact_id']})
        with self.assertRaises(HTTPError) as error:
            self.get(f'/api/topics?id={record["contact_id"]}')
        self.assertEqual(error.exception.code, 400)

    def test_model_probe_route_sends_no_records(self):
        received = []

        class ProbeMock(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                received.append(('GET', self.path, None))
                if self.path == '/health':
                    payload = {'status': 'ok'}
                elif self.path == '/v1/models':
                    payload = {'data': [{'id': 'actual-local-model'}]}
                else:
                    self.send_error(404)
                    return
                data = json.dumps(payload).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(data)

            def do_POST(self):
                received.append(('POST', self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                data = json.dumps({'choices': [{'message': {'content': 'READY'}}]}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(data)

        model_server = ThreadingHTTPServer(('127.0.0.1', 0), ProbeMock)
        thread = threading.Thread(target=model_server.serve_forever, daemon=True); thread.start()
        try:
            result = self.post('/api/model/test', {'config': {
                'endpoint': f'http://127.0.0.1:{model_server.server_port}/v1', 'model': 'local-model'}})
            self.assertEqual(result['status'], 'available')
            self.assertEqual(result['model'], 'actual-local-model')
            posts = [item for item in received if item[0] == 'POST']
            self.assertEqual(len(posts), 1)
            serialized = json.dumps(posts[0], ensure_ascii=False)
            self.assertNotIn('联系人', serialized)
            self.assertNotIn('咖啡', serialized)
        finally:
            model_server.shutdown(); model_server.server_close(); thread.join()

    def test_answer_rejects_other_contact_evidence(self):
        first = self.post('/api/import', {"filename": "owner-a.txt", "base64": base64.b64encode(SAMPLE.encode()).decode(), "name": "证据甲"})
        second = self.post('/api/import', {"filename": "owner-b.txt", "base64": base64.b64encode(SAMPLE.encode()).decode(), "name": "证据乙"})
        other_id = self.store.messages(second['contact_id'])[0]['id']
        with self.assertRaises(HTTPError) as error:
            self.post('/api/answer', {'contact_id': first['contact_id'], 'query': '茶', 'evidence_ids': [other_id],
                                      'allow_send': True, 'config': {'endpoint': 'http://127.0.0.1:1/v1', 'model': 'test'}})
        self.assertEqual(error.exception.code, 400)
        self.assertIn('不属于当前联系人', self.error_json(error.exception)['error'])

    def test_answer_rejects_trashed_contact_evidence(self):
        record = self.post('/api/import', {"filename": "owner-trash.txt", "base64": base64.b64encode(SAMPLE.encode()).decode(), "name": "回收证据"})
        message_id = self.store.messages(record['contact_id'])[0]['id']
        self.post('/api/contact/trash', {'contact_id': record['contact_id']})
        with self.assertRaises(HTTPError) as error:
            self.post('/api/answer', {'contact_id': record['contact_id'], 'query': '茶', 'evidence_ids': [message_id],
                                      'allow_send': True, 'config': {'endpoint': 'http://127.0.0.1:1/v1', 'model': 'test'}})
        self.assertEqual(error.exception.code, 400)


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.reply = json.dumps({"answer_units": [{
            "text": "根据记录，当时更喜欢茶。",
            "kind": "fact",
            "citations": ["M1"],
            "uncertainty": None,
        }]}, ensure_ascii=False)
        self.received = None
        self.requests = 0
        self.payload_override = None
        self.response_status = 200
        self.delay = 0
        test = self

        class Mock(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                test.requests += 1
                test.received = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if test.delay:
                    time.sleep(test.delay)
                if test.response_status == 302:
                    self.send_response(302)
                    self.send_header('Location', 'http://127.0.0.1:9/stolen')
                    self.end_headers()
                    return
                payload = test.payload_override if test.payload_override is not None else {"choices": [{"message": {"content": test.reply}}]}
                data = json.dumps(payload).encode()
                self.send_response(test.response_status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.config = {"endpoint": "http://127.0.0.1:" + str(self.server.server_port) + "/v1", "model": "test"}
        self.evidence = [{
            "id": 1, "contact_id": 1, "speaker": "小林",
            "timestamp": "2026-09-01 18:00:00", "text": "现在更喜欢茶",
            "source_id": 1, "source_name": "synthetic.txt", "line_start": 1, "line_end": 1,
        }]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_model_protocol_and_valid_citations(self):
        response = ai.answer("喜欢喝什么", self.evidence, self.config)
        self.assertEqual(response["citations"], ["M1"])
        self.assertEqual(self.received["model"], "test")
        self.assertNotIn("tools", self.received)
        response_format = self.received["response_format"]
        self.assertEqual("json_schema", response_format["type"])
        self.assertTrue(response_format["json_schema"]["strict"])
        schema = response_format["json_schema"]["schema"]
        self.assertEqual(["answer_units"], schema["required"])
        self.assertFalse(schema["additionalProperties"])
        unit_schema = schema["properties"]["answer_units"]["items"]
        self.assertEqual(
            ["text", "kind", "citations", "uncertainty"],
            unit_schema["required"],
        )
        self.assertFalse(unit_schema["additionalProperties"])
        self.assertEqual(0, self.received["temperature"])
        self.assertEqual({"enable_thinking": False}, self.received["chat_template_kwargs"])
        self.assertEqual(512, self.received["max_tokens"])
        self.assertEqual(len(json.loads(self.received["messages"][1]["content"])["evidence"]), 1)

    def test_fabricated_citation_rejected(self):
        self.reply = json.dumps({"answer_units": [{
            "text": "她喜欢咖啡。", "kind": "fact",
            "citations": ["M999"], "uncertainty": None,
        }]}, ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, "不存在的引用"):
            ai.answer("喜欢喝什么", self.evidence, self.config)

    def test_remote_plain_http_rejected(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            ai.answer("喜欢喝什么", self.evidence, {"endpoint": "http://example.com/v1", "model": "test"})

    def test_probe_sends_no_chat_data(self):
        response = ai.test_connection(self.config)
        self.assertEqual(response['status'], 'connected')
        self.assertEqual(response['destination'], '127.0.0.1')
        self.assertEqual(self.requests, 1)
        serialized = json.dumps(self.received, ensure_ascii=False)
        self.assertNotIn('现在更喜欢茶', serialized)
        self.assertNotIn('小林', serialized)
        self.assertNotIn('喜欢喝什么', serialized)
        self.assertEqual(self.received['max_tokens'], 8)
        self.assertEqual(self.received['messages'], [{'role': 'user', 'content': 'Reply with READY.'}])

    def test_probe_rejects_malformed_response(self):
        self.payload_override = {'choices': []}
        with self.assertRaisesRegex(ValueError, 'Chat Completions'):
            ai.test_connection(self.config)
        self.assertEqual(self.requests, 1)

    def test_answer_without_citation_rejected(self):
        self.reply = json.dumps({"answer_units": [{
            "text": "根据记录，当时更喜欢茶。", "kind": "fact",
            "citations": [], "uncertainty": None,
        }]}, ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, '引用'):
            ai.answer('喜欢喝什么', self.evidence, self.config)

    def test_non_json_answer_rejected(self):
        self.reply = '这不是 JSON。'
        with self.assertRaisesRegex(ValueError, 'JSON'):
            ai.answer_units('喜欢喝什么', self.evidence, self.config)

    def test_evidence_requests_reject_remote_https_targets(self):
        with self.assertRaisesRegex(ValueError, '仅允许发送到本机'):
            ai.answer_units(
                '喜欢喝什么', self.evidence,
                {'endpoint': 'https://example.com/v1', 'model': 'test'},
            )

    def test_redirect_rejected(self):
        self.response_status = 302
        with self.assertRaisesRegex(ValueError, 'HTTP 302'):
            ai.test_connection(self.config)
        self.assertEqual(self.requests, 1)

    def test_timeout_does_not_retry(self):
        self.delay = 0.2
        with patch('ai.REQUEST_TIMEOUT', 0.03):
            with self.assertRaisesRegex(ValueError, '连接失败或超时'):
                ai.answer('喜欢喝什么', self.evidence, self.config)
        self.assertEqual(self.requests, 1)

    def test_endpoint_credentials_query_and_fragment_rejected(self):
        for endpoint in ('http://user:pass@127.0.0.1:8080/v1',
                         'http://127.0.0.1:8080/v1?secret=x',
                         'http://127.0.0.1:8080/v1#fragment'):
            with self.assertRaisesRegex(ValueError, '不能含'):
                ai.validate_config({'endpoint': endpoint, 'model': 'test'})


if __name__ == "__main__":
    unittest.main()
