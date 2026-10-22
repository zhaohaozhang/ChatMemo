import json
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import ai
from llama_runtime import LlamaConfig, LlamaRuntime, load_config, save_config


class FakeProcess:
    def __init__(self, pid=4321):
        self.pid = pid
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0
        self.wait_timeout = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1
        self.returncode = -9
        self.wait_timeout = False

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_timeout:
            raise subprocess.TimeoutExpired('fake', timeout)
        self.returncode = 0
        return self.returncode


class LlamaRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.exe = root / 'llama-server.exe'
        self.model = root / 'synthetic.gguf'
        self.exe.write_bytes(b'exe')
        self.model.write_bytes(b'model')
        self.config = LlamaConfig.from_dict({
            'exe_path': str(self.exe),
            'model_path': str(self.model),
            'port': 18080,
            'context_size': 4096,
            'gpu_layers': 24,
            'threads': 6,
        })
        self.created = []
        self.popen_calls = []

        def fake_popen(args, **kwargs):
            process = FakeProcess(pid=4300 + len(self.created))
            self.created.append(process)
            self.popen_calls.append((args, kwargs))
            return process

        self.fake_popen = fake_popen

    def tearDown(self):
        self.tmp.cleanup()

    def runtime(self, port_in_use=lambda *_: False, probe=lambda *_: False):
        return LlamaRuntime(
            popen_factory=self.fake_popen,
            port_in_use=port_in_use,
            service_probe=probe,
        )

    def test_config_validates_paths_ranges_and_unknown_sensitive_fields(self):
        self.assertEqual('127.0.0.1', self.config.host)
        bad_values = (
            {**self.config.to_dict(), 'exe_path': str(self.exe.parent / 'missing.exe')},
            {**self.config.to_dict(), 'model_path': str(self.exe.parent / 'missing.gguf')},
            {**self.config.to_dict(), 'port': 70000},
            {**self.config.to_dict(), 'context_size': 1},
            {**self.config.to_dict(), 'question': '真实聊天内容'},
            {**self.config.to_dict(), 'api_key': 'secret'},
        )
        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    LlamaConfig.from_dict(value)

    def test_config_round_trip_is_atomic_and_contains_only_launch_settings(self):
        path = Path(self.tmp.name) / 'runtime' / 'llama.json'
        save_config(path, self.config)
        loaded = load_config(path)
        self.assertEqual(self.config, loaded)
        payload = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(
            {'exe_path', 'model_path', 'port', 'context_size', 'gpu_layers', 'threads'},
            set(payload),
        )
        self.assertFalse((path.parent / 'llama.json.tmp').exists())
        self.assertIsNone(load_config(path.parent / 'absent.json'))

    def test_start_uses_argument_array_hidden_process_and_fixed_loopback(self):
        runtime = self.runtime()
        result = runtime.start(self.config)
        args, kwargs = self.popen_calls[0]
        self.assertIsInstance(args, list)
        self.assertEqual(False, kwargs['shell'])
        self.assertEqual('127.0.0.1', args[args.index('--host') + 1])
        self.assertEqual(str(self.model), args[args.index('-m') + 1])
        self.assertNotIn('聊天', ' '.join(args))
        self.assertTrue(result['owner_token'])
        self.assertEqual('loading', result['state'])

    def test_duplicate_start_slow_loading_available_and_abnormal_exit(self):
        compatible = {'value': False}
        runtime = self.runtime(probe=lambda *_: compatible['value'])
        first = runtime.start(self.config)
        duplicate = runtime.start(self.config)
        self.assertEqual(first['owner_token'], duplicate['owner_token'])
        self.assertEqual(1, len(self.created))
        self.assertEqual('loading', runtime.status()['state'])
        compatible['value'] = True
        self.assertEqual('available', runtime.status()['state'])
        self.created[0].returncode = 3
        self.assertEqual('abnormal_exit', runtime.status()['state'])

    def test_concurrent_start_creates_only_one_owned_process(self):
        entered = threading.Event()
        release = threading.Event()
        created = []

        def slow_popen(*args, **kwargs):
            process = FakeProcess(pid=4400 + len(created))
            created.append(process)
            entered.set()
            release.wait(2)
            return process

        runtime = LlamaRuntime(
            popen_factory=slow_popen,
            port_in_use=lambda *_: False,
            service_probe=lambda *_: False,
        )
        results = []
        first = threading.Thread(target=lambda: results.append(runtime.start(self.config)))
        second = threading.Thread(target=lambda: results.append(runtime.start(self.config)))
        first.start()
        self.assertTrue(entered.wait(1))
        second.start()
        time.sleep(0.1)
        release.set()
        first.join(2)
        second.join(2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(1, len(created))
        self.assertEqual(2, len(results))
        self.assertEqual(results[0]['owner_token'], results[1]['owner_token'])

    def test_external_compatible_service_and_port_conflict_are_never_stopped(self):
        compatible = self.runtime(port_in_use=lambda *_: True, probe=lambda *_: True)
        result = compatible.start(self.config)
        self.assertEqual('external_service', result['state'])
        self.assertEqual(0, len(self.created))
        self.assertFalse(compatible.stop('anything')['stopped'])

        conflict = self.runtime(port_in_use=lambda *_: True, probe=lambda *_: False)
        self.assertEqual('port_conflict', conflict.start(self.config)['state'])
        self.assertFalse(conflict.stop('anything')['stopped'])

    def test_wrong_token_exited_handle_and_pid_without_handle_never_terminate(self):
        runtime = self.runtime()
        started = runtime.start(self.config)
        process = self.created[0]
        self.assertFalse(runtime.stop('wrong-token')['stopped'])
        self.assertEqual(0, process.terminate_calls)
        process.returncode = 2
        self.assertFalse(runtime.stop(started['owner_token'])['stopped'])
        self.assertEqual(0, process.terminate_calls)

        restarted = self.runtime()
        restarted._pid = process.pid
        self.assertFalse(restarted.stop(started['owner_token'])['stopped'])
        self.assertEqual(0, process.terminate_calls)

    def test_owned_live_process_terminates_and_kills_only_after_timeout(self):
        runtime = self.runtime()
        started = runtime.start(self.config)
        process = self.created[0]
        process.wait_timeout = True
        result = runtime.stop(started['owner_token'])
        self.assertTrue(result['stopped'])
        self.assertEqual(1, process.terminate_calls)
        self.assertEqual(1, process.kill_calls)


class LlamaProbeTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.reasoning_only = False
        test = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                test.requests.append(('GET', self.path, None))
                if self.path == '/health':
                    payload = {'status': 'ok'}
                elif self.path == '/v1/models':
                    payload = {'data': [{'id': 'actual-local-model'}]}
                else:
                    self.send_error(404)
                    return
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                test.requests.append(('POST', self.path, body))
                message = (
                    {'content': '', 'reasoning_content': 'Thinking about READY'}
                    if test.reasoning_only else {'content': 'READY'}
                )
                data = json.dumps({'choices': [{'message': message}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f'http://127.0.0.1:{self.server.server_port}/v1'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_model_discovery_and_probe_select_actual_id_without_chat_data(self):
        self.assertEqual(['actual-local-model'], ai.discover_models(self.endpoint))
        result = ai.probe_local({'endpoint': self.endpoint, 'model': 'placeholder'})
        self.assertEqual('actual-local-model', result['model'])
        serialized = json.dumps(self.requests, ensure_ascii=False)
        for private in ('小林', '聊天正文', '真实问题', 'example.txt'):
            self.assertNotIn(private, serialized)
        posts = [item for item in self.requests if item[0] == 'POST']
        self.assertEqual(1, len(posts))
        self.assertEqual('actual-local-model', posts[0][2]['model'])

    def test_probe_accepts_qwen_reasoning_only_short_completion(self):
        self.reasoning_only = True
        result = ai.probe_local({'endpoint': self.endpoint, 'model': 'placeholder'})
        self.assertEqual('available', result['status'])
        self.assertEqual('actual-local-model', result['model'])


if __name__ == '__main__':
    unittest.main()
