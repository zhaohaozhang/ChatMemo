"""Own and manage one local llama.cpp server process without touching outsiders."""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LlamaConfig:
    exe_path: Path
    model_path: Path
    port: int = 8080
    context_size: int = 8192
    gpu_layers: int = 0
    threads: int = 4

    @property
    def host(self):
        return "127.0.0.1"

    @property
    def endpoint(self):
        return f"http://{self.host}:{self.port}/v1"

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("llama.cpp 配置格式无效。")
        allowed = {"exe_path", "model_path", "port", "context_size", "gpu_layers", "threads"}
        if set(value) - allowed:
            raise ValueError("llama.cpp 配置包含不允许的字段。")
        try:
            exe_path = Path(value["exe_path"]).expanduser().resolve()
            model_path = Path(value["model_path"]).expanduser().resolve()
            port = int(value.get("port", 8080))
            context_size = int(value.get("context_size", 8192))
            gpu_layers = int(value.get("gpu_layers", 0))
            threads = int(value.get("threads", 4))
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError("llama.cpp 配置字段无效。") from None
        if not exe_path.is_file():
            raise ValueError("llama-server.exe 路径不存在或不是文件。")
        if not model_path.is_file() or model_path.suffix.lower() != ".gguf":
            raise ValueError("GGUF 模型路径不存在或格式不正确。")
        if not 1024 <= port <= 65535:
            raise ValueError("端口应在 1024–65535 之间。")
        if not 512 <= context_size <= 262144:
            raise ValueError("上下文长度应在 512–262144 之间。")
        if not -1 <= gpu_layers <= 1000:
            raise ValueError("GPU 层数应在 -1–1000 之间。")
        if not 1 <= threads <= 256:
            raise ValueError("线程数应在 1–256 之间。")
        return cls(exe_path, model_path, port, context_size, gpu_layers, threads)

    def to_dict(self):
        return {
            "exe_path": str(self.exe_path),
            "model_path": str(self.model_path),
            "port": self.port,
            "context_size": self.context_size,
            "gpu_layers": self.gpu_layers,
            "threads": self.threads,
        }


def load_config(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        return LlamaConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError("无法读取 llama.cpp 本地配置。") from error


def save_config(path, config):
    if not isinstance(config, LlamaConfig):
        raise ValueError("llama.cpp 配置格式无效。")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _port_in_use(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


class LlamaRuntime:
    def __init__(self, popen_factory=None, port_in_use=None, service_probe=None):
        self._popen = popen_factory or subprocess.Popen
        self._port_in_use = port_in_use or _port_in_use
        self._service_probe = service_probe or (lambda endpoint: False)
        self._process = None
        self._pid = None
        self._owner_token = None
        self._config = None
        self._external = False
        self._state = "unconfigured"
        self._exit_code = None
        self._lock = threading.RLock()

    def _probe(self):
        if self._config is None:
            return False
        try:
            result = self._service_probe(self._config.endpoint)
            if isinstance(result, dict):
                return result.get("status") in {"connected", "available", "ok"}
            return bool(result)
        except Exception:
            return False

    def _public(self, include_token=False):
        result = {
            "state": self._state,
            "pid": self._pid,
            "endpoint": self._config.endpoint if self._config else None,
            "owned": self._process is not None and not self._external,
            "can_stop": bool(
                self._process is not None
                and self._process.poll() is None
                and self._owner_token
            ),
        }
        if self._exit_code is not None:
            result["exit_code"] = self._exit_code
        if include_token and self._owner_token:
            result["owner_token"] = self._owner_token
        return result

    def start(self, config):
        with self._lock:
            return self._start(config)

    def _start(self, config):
        if not isinstance(config, LlamaConfig):
            raise ValueError("llama.cpp 配置格式无效。")
        if self._process is not None and self._process.poll() is None:
            return self._public(include_token=True)
        self._config = config
        self._process = None
        self._pid = None
        self._owner_token = None
        self._external = False
        self._exit_code = None
        if self._port_in_use(config.host, config.port):
            if self._probe():
                self._external = True
                self._state = "external_service"
            else:
                self._state = "port_conflict"
            return self._public()
        args = [
            str(config.exe_path),
            "-m", str(config.model_path),
            "--host", config.host,
            "--port", str(config.port),
            "--ctx-size", str(config.context_size),
            "--n-gpu-layers", str(config.gpu_layers),
            "--threads", str(config.threads),
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = self._popen(
                args,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as error:
            self._state = "start_failed"
            raise ValueError(f"无法启动 llama.cpp：{error}") from None
        self._process = process
        self._pid = process.pid
        self._owner_token = secrets.token_urlsafe(24)
        self._state = "loading"
        return self._public(include_token=True)

    def status(self):
        with self._lock:
            return self._status()

    def _status(self):
        if self._process is not None:
            exit_code = self._process.poll()
            if exit_code is not None:
                self._exit_code = exit_code
                self._state = "abnormal_exit" if exit_code else "stopped"
            elif self._probe():
                self._state = "available"
            else:
                self._state = "loading"
        elif self._external and self._probe():
            self._state = "external_service"
        return self._public()

    def stop(self, owner_token):
        with self._lock:
            return self._stop(owner_token)

    def _stop(self, owner_token):
        process = self._process
        if (
            process is None
            or not self._owner_token
            or not isinstance(owner_token, str)
            or not secrets.compare_digest(owner_token, self._owner_token)
            or process.poll() is not None
        ):
            return {**self.status(), "stopped": False, "reason": "not_owned_or_not_running"}
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        self._state = "stopped"
        self._exit_code = process.poll()
        self._process = None
        self._pid = None
        self._owner_token = None
        return {**self._public(), "stopped": True}
