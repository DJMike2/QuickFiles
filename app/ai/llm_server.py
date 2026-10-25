"""Own one llama-server subprocess; GPU startup can fall back to CPU."""
from __future__ import annotations

import atexit
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


class LLMServerError(RuntimeError):
    pass


class ContextTooLargeError(LLMServerError):
    pass


class LLMServer:
    def __init__(self, binary_manager, model_path, settings, *, embedding=False):
        self.binary_manager = binary_manager
        self.model_path = model_path
        self.settings = settings
        self.embedding = embedding
        self._process = None
        self._output = None
        self.port = 0
        self.backend = "cpu"

    @property
    def is_running(self):
        return self._process is not None and self._process.poll() is None

    def start(self, status_cb=None, cancelled=None):
        if self.is_running:
            return
        status_cb = status_cb or (lambda message: None)
        cancelled = cancelled or (lambda: False)
        manager = self.binary_manager
        if not manager.is_available or not self.model_path.is_file():
            raise LLMServerError("AI engine or model is missing")
        attempts = []
        if manager.gpu_binary_path and not self.embedding:
            attempts.append((manager.gpu_binary_path, self.settings["n_gpu_layers"], manager.hardware.backend))
        metal = manager.hardware.backend == "metal" and not self.embedding
        attempts.append((manager.cpu_binary_path, self.settings["n_gpu_layers"] if metal else 0,
                         "metal" if metal else "cpu"))
        errors = []
        for binary, layers, backend in attempts:
            if cancelled():
                raise InterruptedError("Model loading stopped")
            try:
                # Choose a free local port without terminating unrelated processes.
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    self.port = sock.getsockname()[1]
                environment = dict(os.environ)
                environment["PATH"] = str(binary.parent) + os.pathsep + environment.get("PATH", "")
                if sys.platform.startswith("linux"):
                    environment["LD_LIBRARY_PATH"] = str(binary.parent) + os.pathsep + environment.get("LD_LIBRARY_PATH", "")
                self._output = tempfile.TemporaryFile()
                command = [str(binary), "--model", str(self.model_path), "--host", "127.0.0.1",
                     "--port", str(self.port), "--ctx-size", str(self.settings["context_tokens"]),
                     "--n-gpu-layers", str(layers), "--threads", str(self.settings["threads"])]
                if self.embedding:
                    command += ["--embedding", "--pooling", "mean", "--parallel", "1",
                                "--batch-size", "2048", "--ubatch-size", "2048"]
                self._process = subprocess.Popen(
                    command,
                    stdout=self._output, stderr=subprocess.STDOUT, cwd=binary.parent, env=environment,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                atexit.register(self.stop)
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    if cancelled():
                        raise InterruptedError("Model loading stopped")
                    if not self.is_running:
                        self._output.seek(0)
                        detail = self._output.read(4096).decode(errors="replace")
                        raise LLMServerError(f"Engine exited during startup: {detail}")
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=1) as response:
                            if response.status == 200:
                                self.backend = backend
                                return
                    except (OSError, urllib.error.URLError):
                        pass
                    status_cb(f"Loading model ({backend.upper()})…")
                    time.sleep(0.3)
                raise LLMServerError("Model loading timed out")
            except InterruptedError:
                self.stop()
                raise
            except (OSError, LLMServerError) as exc:
                errors.append(str(exc))
                self.stop()
                status_cb("Trying the CPU engine…")
        raise LLMServerError("; ".join(errors))

    def chat(self, system, user, max_tokens, temperature, *, schema=None, grammar=None):
        if not self.is_running:
            raise LLMServerError("The local AI engine is not running")
        payload = {
            "model": "local", "messages": [
                {"role": "system", "content": system}, {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens, "temperature": temperature, "stream": False,
            "top_p": self.settings["top_p"], "top_k": self.settings["top_k"],
            "min_p": self.settings["min_p"], "seed": self.settings["seed"],
            "cache_prompt": True, "chat_template_kwargs": {"enable_thinking": False},
        }
        if schema is not None:
            payload["json_schema"] = schema
        if grammar is not None:
            payload["grammar"] = grammar
        try:
            choice = self._request("chat/completions", payload)["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise LLMServerError("The model did not finish its response. No partial plan will be used.")
            if choice["message"].get("reasoning_content"):
                raise LLMServerError("The model returned thinking output despite thinking being disabled")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise LLMServerError("The model returned an empty response")
            return content
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMServerError(f"Invalid local AI response: {exc}") from exc

    def embed(self, texts):
        if not self.is_running:
            raise LLMServerError("The local embedding engine is not running")
        try:
            rows = self._request("embeddings", {"input": texts, "model": "local"})["data"]
            rows = sorted(rows, key=lambda row: row["index"])
            if [row["index"] for row in rows] != list(range(len(texts))):
                raise ValueError("Missing embedding vectors")
            return [row["embedding"] for row in rows]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMServerError(f"Invalid embedding response: {exc}") from exc

    def _request(self, endpoint, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/{endpoint}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read(4096).decode(errors="replace")
            if exc.code == 400 and any(word in detail.lower() for word in ("context", "exceed", "too large")):
                raise ContextTooLargeError(detail) from exc
            raise LLMServerError(f"Engine HTTP {exc.code}: {detail}") from exc
        except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMServerError(f"Local AI request failed: {exc}") from exc

    def stop(self):
        atexit.unregister(self.stop)
        if self._process is not None:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()
            self._process = None
        if self._output is not None:
            self._output.close()
            self._output = None
