"""Run: python server.py --port 8765. Private, loopback-only desktop utility."""
import argparse
import base64
import binascii
import hashlib
import json
import mimetypes
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import ai
from llama_runtime import LlamaConfig, LlamaRuntime, load_config, save_config
from parser import decode_text, parse_text
from qa import answer_question
from search import search
from store import Store
from topics import build_overview

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"


def make_handler(store, runtime=None, config_path=None):
    runtime = runtime or LlamaRuntime(
        service_probe=lambda endpoint: ai.probe_local({"endpoint": endpoint, "model": "local-model"})
    )
    config_path = Path(config_path or (ROOT / "runtime" / "llama.json"))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Never log private questions, raw text, or API keys.

        def valid_host(self):
            return self.headers.get("Host") in {
                f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"
            }

        def send_bytes(self, data, content_type, status=200, filename=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                             "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(filename))
            self.end_headers()
            self.wfile.write(data)

        def json(self, value, status=200):
            self.send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"),
                            "application/json; charset=utf-8", status)

        def do_GET(self):
            if not self.valid_host():
                return self.json({"error": "仅允许从本机地址访问。"}, 403)
            url = urlsplit(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/api/contacts":
                    return self.json({"contacts": store.contacts()})
                if url.path == "/api/trash":
                    return self.json({"contacts": store.trash()})
                if url.path == "/api/contact":
                    contact_id = int(query["id"])
                    contact = store.contact(contact_id)
                    return self.json({**contact, "speakers": sorted({m["speaker"] for m in store.messages(contact_id)})})
                if url.path == "/api/context":
                    return self.json(store.context(int(query["id"])))
                if url.path == "/api/topics":
                    contact_id = int(query["id"])
                    store.contact(contact_id)
                    return self.json(build_overview(store.messages(contact_id)))
                if url.path == "/api/llama/config":
                    config = load_config(config_path)
                    return self.json({
                        "configured": config is not None,
                        "config": config.to_dict() if config else None,
                    })
                if url.path == "/api/llama/status":
                    return self.json(runtime.status())
                if url.path == "/api/demo":
                    return self.json({"filename": "小林-虚构示例.txt",
                                      "base64": base64.b64encode((ROOT / "examples" / "小林-虚构示例.txt").read_bytes()).decode("ascii")})
                if url.path == "/api/source":
                    source = store.source(int(query["id"]))
                    return self.send_bytes(source["raw"], "application/octet-stream", filename=source["name"])
                if url.path == "/api/export":
                    contact_id = int(query["id"])
                    name = store.contact(contact_id)["name"]
                    lines = [f"# {name} · 聊天原文", "", "> 从本地聊天记忆库导出；正文均为聊天资料。", ""]
                    for message in store.messages(contact_id):
                        lines.extend([f"## {message['timestamp']} · {message['speaker']} ^m{message['id']}",
                                      f"来源：{message['source_name']}，第 {message['line_start']}–{message['line_end']} 行", "",
                                      *["> " + line for line in message["text"].splitlines()], ""])
                    return self.send_bytes("\n".join(lines).encode("utf-8"), "text/markdown; charset=utf-8",
                                           filename=name + "-聊天原文.md")
                file = WEB / ("index.html" if url.path == "/" else url.path.lstrip("/"))
                if not file.resolve().is_relative_to(WEB.resolve()) or not file.is_file():
                    return self.json({"error": "页面不存在。"}, 404)
                content_type = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
                return self.send_bytes(file.read_bytes(), content_type + ("; charset=utf-8" if file.suffix in (".html", ".css", ".js", ".svg") else ""))
            except (ValueError, KeyError) as error:
                return self.json({"error": str(error)}, 400)
            except Exception:
                return self.json({"error": "读取失败，请稍后重试。"}, 500)

        def do_POST(self):
            if not self.valid_host():
                return self.json({"error": "仅允许从本机地址访问。"}, 403)
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                return self.json({"error": "不允许跨来源请求。"}, 403)
            if self.headers.get("X-Memory-Client") != "1" or not self.headers.get("Content-Type", "").startswith("application/json"):
                return self.json({"error": "请求格式无效。"}, 403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8 * 1024 * 1024:
                    raise ValueError("请求过大或为空；单个文件最多 5 MB。")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("请求内容必须是对象。")
                path = urlsplit(self.path).path
                if path == "/api/contacts":
                    return self.json(store.create_contact(body.get("name")))
                if path == "/api/contact/trash":
                    return self.json(store.trash_contact(int(body["contact_id"])))
                if path == "/api/contact/restore":
                    return self.json(store.restore_contact(int(body["contact_id"])))
                if path == "/api/contact/purge":
                    return self.json(store.purge_contact(int(body["contact_id"]), body.get("exact_name")))
                if path == "/api/model/test":
                    return self.json(ai.probe_local(body.get("config", {})))
                if path == "/api/llama/config":
                    config = LlamaConfig.from_dict(body)
                    save_config(config_path, config)
                    return self.json({"configured": True, "config": config.to_dict()})
                if path == "/api/llama/start":
                    config = load_config(config_path)
                    if config is None:
                        raise ValueError("请先保存 llama.cpp 配置。")
                    return self.json(runtime.start(config))
                if path == "/api/llama/stop":
                    return self.json(runtime.stop(body.get("owner_token", "")))
                if path in ("/api/preview", "/api/import"):
                    filename = str(body.get("filename", ""))
                    if not filename.lower().endswith(".txt"):
                        raise ValueError("当前支持 .txt 文件。")
                    raw = base64.b64decode(body["base64"], validate=True)
                    text, encoding = decode_text(raw)
                    if path == "/api/preview":
                        parsed = parse_text(text)
                        proposed_name = str(body.get("name", "") or parsed["contact_name"] or "").strip()
                        return self.json({
                            "count": len(parsed["messages"]), "encoding": encoding,
                            "messages": parsed["messages"][:15], "warnings": parsed["warnings"],
                            "unparsed": parsed["unparsed"][:12], "unparsed_count": len(parsed["unparsed"]),
                            "contact_name": parsed["contact_name"], "speakers": parsed["speakers"],
                            "first_at": parsed["messages"][0]["timestamp"] if parsed["messages"] else None,
                            "last_at": parsed["messages"][-1]["timestamp"] if parsed["messages"] else None,
                            "trash_matches": store.trash_matches(proposed_name, hashlib.sha256(raw).hexdigest()),
                        })
                    contact_id = body.get("contact_id")
                    requested_name = str(body.get("name", ""))
                    if not contact_id:
                        parsed = parse_text(text)
                        proposed_name = (requested_name or parsed["contact_name"] or "未命名联系人").strip()
                        matches = store.trash_matches(proposed_name, hashlib.sha256(raw).hexdigest())
                        action = body.get("trash_conflict_action")
                        if matches and action != "new":
                            raise ValueError("回收站中存在同名档案或相同文件，请明确选择恢复、仍作为新联系人导入或取消。")
                        if action not in (None, "new"):
                            raise ValueError("回收站冲突选择无效。")
                    return self.json(store.import_text(filename, text, raw, name=requested_name,
                                                       contact_id=int(contact_id) if contact_id else None))
                if path == "/api/qa":
                    contact_id = int(body["contact_id"])
                    config = body.get("config")
                    summarizer = None
                    if config is not None and body.get("use_model") is True:
                        summarizer = lambda question, evidence: ai.answer_units(question, evidence, config)
                    result = answer_question(
                        store,
                        contact_id,
                        body.get("question", ""),
                        body.get("filters", {}),
                        body.get("previous"),
                        summarizer=summarizer,
                    )
                    return self.json(result)
                if path in ("/api/search", "/api/answer"):
                    contact_id = int(body["contact_id"])
                    try:
                        store.contact(contact_id)
                    except ValueError:
                        raise ValueError("请先选择有效联系人。") from None
                    query = body.get("query", "")
                    result = search(store, contact_id, query,
                                    str(body.get("date_from", "")), str(body.get("date_to", "")),
                                    str(body.get("speaker", "")))
                    if path == "/api/search":
                        return self.json(result)
                    if body.get("allow_send") is not True:
                        raise ValueError("请确认向选定模型发送所选片段。")
                    chosen = {int(i) for i in body.get("evidence_ids", [])}
                    available = {m["id"]: m for m in store.messages(contact_id)}
                    if not chosen or not chosen.issubset(available):
                        raise ValueError("所选原文不存在或不属于当前联系人。")
                    evidence = [available[i] for i in sorted(chosen)]
                    return self.json(ai.answer(query, evidence, body.get("config", {})))
                return self.json({"error": "接口不存在。"}, 404)
            except (ValueError, KeyError, TypeError, binascii.Error) as error:
                return self.json({"error": str(error)}, 400)
            except sqlite3.Error:
                return self.json({"error": "保存失败，请检查数据目录权限并重试。"}, 500)
            except Exception:
                return self.json({"error": "处理失败，请稍后重试。"}, 500)
    return Handler


def main():
    args = argparse.ArgumentParser(description="聊天记忆：本地 TXT 检索")
    args.add_argument("--port", type=int, default=8765)
    args.add_argument("--data-dir", default=str(ROOT / "data"))
    options = args.parse_args()
    store = Store(Path(options.data_dir) / "memory.sqlite3")
    server = ThreadingHTTPServer(("127.0.0.1", options.port), make_handler(store))
    print(f"Chat Memory: http://127.0.0.1:{server.server_port}", flush=True)
    print("Data is local. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
