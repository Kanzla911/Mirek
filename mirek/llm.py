"""Minimaler Ollama-Client (nur Standardbibliothek)."""

import json
import urllib.error
import urllib.request


class OllamaError(Exception):
    pass


class Ollama:
    def __init__(self, host, model, num_ctx=16384, temperature=0.3, think=False):
        self.host = host.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.think = think
        self._send_think = True

    @classmethod
    def from_config(cls, cfg):
        return cls(cfg["ollama_host"], cfg["model"], cfg["num_ctx"], cfg["temperature"], cfg["think"])

    def _request(self, method, path, payload=None, timeout=10):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            self.host + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                msg = json.loads(body).get("error", body)
            except ValueError:
                msg = body
            raise OllamaError(msg or str(e)) from None
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise OllamaError(f"Ollama ist nicht erreichbar unter {self.host} ({reason})") from None

    def _stream(self, path, payload, timeout=600):
        with self._request("POST", path, payload, timeout) as resp:
            for line in resp:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                if data.get("error"):
                    raise OllamaError(data["error"])
                yield data

    def version(self):
        with self._request("GET", "/api/version", timeout=3) as r:
            return json.load(r).get("version", "?")

    def models(self):
        with self._request("GET", "/api/tags", timeout=5) as r:
            return [m["name"] for m in json.load(r).get("models", [])]

    def has_model(self, name=None):
        name = name or self.model
        names = self.models()
        return name in names or (":" not in name and f"{name}:latest" in names)

    def pull(self, name=None):
        yield from self._stream("/api/pull", {"model": name or self.model, "stream": True}, timeout=3600)

    def chat(self, messages, tools=None):
        """Streamt Teile der Antwort: {"content"}, {"thinking"}, {"tool_calls"}."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {"num_ctx": self.num_ctx, "temperature": self.temperature},
        }
        if tools:
            payload["tools"] = tools
        if self._send_think:
            payload["think"] = self.think
        started = False
        try:
            for data in self._stream("/api/chat", payload):
                started = True
                msg = data.get("message") or {}
                part = {k: msg[k] for k in ("content", "thinking", "tool_calls") if msg.get(k)}
                if part:
                    yield part
        except OllamaError as e:
            # Ältere Ollama-Versionen / Modelle ohne Denk-Modus kennen "think" nicht.
            if not started and self._send_think and "think" in str(e).lower():
                self._send_think = False
                yield from self.chat(messages, tools)
                return
            raise
