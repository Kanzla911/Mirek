"""KI-Anbindung: Ollama oder jeder OpenAI-kompatible lokale Server (LM Studio, llama.cpp, Jan, vLLM …).

Intern speichert Mirek Nachrichten im Ollama-Format, plus:
  image_paths  – Bilder als Dateipfade (werden erst beim Senden eingelesen)
  tool_call_id – Zuordnung von Werkzeug-Ergebnissen
"""

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path


class OllamaError(Exception):
    pass


def image_b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode()


def _mime(path):
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}.get(
        Path(path).suffix.lower(), "image/png")


class _HTTP:
    host = ""
    headers = {}

    def _request(self, method, path, payload=None, timeout=10):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.host + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", **self.headers})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                err = json.loads(body).get("error", body)
                msg = err.get("message", str(err)) if isinstance(err, dict) else err
            except (ValueError, AttributeError):
                msg = body
            raise OllamaError(msg or str(e)) from None
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise OllamaError(f"KI-Server nicht erreichbar unter {self.host} ({reason})") from None


class Ollama(_HTTP):
    name = "ollama"
    can_pull = True

    def __init__(self, host, model, num_ctx=16384, temperature=0.3, think=False):
        self.host = host.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.think = think
        self._send_think = True
        self._caps = {}

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

    def model_details(self):
        with self._request("GET", "/api/tags", timeout=5) as r:
            return [{"name": m["name"], "size": m.get("size", 0)} for m in json.load(r).get("models", [])]

    def has_model(self, name=None):
        name = name or self.model
        names = self.models()
        return name in names or (":" not in name and f"{name}:latest" in names)

    def capabilities(self, name=None):
        """z.B. {'completion', 'tools', 'vision', 'thinking'} – leer, wenn unbekannt."""
        name = name or self.model
        if name not in self._caps:
            try:
                with self._request("POST", "/api/show", {"model": name}, timeout=10) as r:
                    self._caps[name] = set(json.load(r).get("capabilities") or [])
            except OllamaError:
                return set()
        return self._caps[name]

    def pull(self, name=None):
        yield from self._stream("/api/pull", {"model": name or self.model, "stream": True}, timeout=3600)

    def delete(self, name):
        with self._request("DELETE", "/api/delete", {"model": name}, timeout=30):
            pass

    @staticmethod
    def convert(messages, vision):
        out = []
        for m in messages:
            msg = {"role": m["role"], "content": m.get("content") or ""}
            if m.get("tool_calls"):
                msg["tool_calls"] = [{"function": c["function"]} for c in m["tool_calls"]]
            if m.get("tool_name"):
                msg["tool_name"] = m["tool_name"]
            if vision and m.get("image_paths"):
                imgs = [image_b64(p) for p in m["image_paths"] if Path(p).exists()]
                if imgs:
                    msg["images"] = imgs
            out.append(msg)
        return out

    def chat(self, messages, tools=None, vision=False):
        """Streamt Teile der Antwort: {"content"}, {"thinking"}, {"tool_calls"}."""
        payload = {
            "model": self.model,
            "messages": self.convert(messages, vision),
            "stream": True,
            "keep_alive": "30m",
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
                yield from self.chat(messages, tools, vision)
                return
            raise


class OpenAICompat(_HTTP):
    """Für LM Studio (Port 1234), llama.cpp-Server, Jan, LocalAI, vLLM, Ollama /v1 …"""

    name = "openai"
    can_pull = False

    def __init__(self, base_url, model, api_key="", temperature=0.3, num_ctx=16384):
        self.host = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def version(self):
        self.models()
        return "OpenAI-kompatibel"

    def models(self):
        with self._request("GET", "/models", timeout=5) as r:
            return [m["id"] for m in json.load(r).get("data", [])]

    def model_details(self):
        return [{"name": m, "size": 0} for m in self.models()]

    def has_model(self, name=None):
        names = self.models()
        return (name or self.model) in names or not names

    def capabilities(self, name=None):
        return {"completion", "tools"}

    def pull(self, name=None):
        raise OllamaError("Modelle bitte in deinem KI-Programm (z.B. LM Studio) herunterladen.")

    def delete(self, name):
        raise OllamaError("Modelle bitte in deinem KI-Programm (z.B. LM Studio) löschen.")

    @staticmethod
    def convert(messages, vision):
        out = []
        for m in messages:
            role, content = m["role"], m.get("content") or ""
            if role == "tool":
                out.append({"role": "tool", "tool_call_id": m.get("tool_call_id", ""), "content": content})
                continue
            msg = {"role": role, "content": content}
            if vision and m.get("image_paths"):
                parts = [{"type": "text", "text": content}]
                for p in m["image_paths"]:
                    if Path(p).exists():
                        parts.append({"type": "image_url",
                                      "image_url": {"url": f"data:{_mime(p)};base64,{image_b64(p)}"}})
                msg["content"] = parts
            if m.get("tool_calls"):
                msg["tool_calls"] = [{
                    "id": c.get("id", ""), "type": "function",
                    "function": {"name": c["function"]["name"],
                                 "arguments": json.dumps(c["function"].get("arguments") or {}, ensure_ascii=False)},
                } for c in m["tool_calls"]]
            out.append(msg)
        return out

    def chat(self, messages, tools=None, vision=False):
        payload = {"model": self.model, "messages": self.convert(messages, vision), "stream": True,
                   "temperature": self.temperature}
        if tools:
            payload["tools"] = tools
        calls = {}
        with self._request("POST", "/chat/completions", payload, timeout=600) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("error"):
                    err = chunk["error"]
                    raise OllamaError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    thinking = delta.get("reasoning_content") or delta.get("reasoning")
                    if thinking:
                        yield {"thinking": thinking}
                    if delta.get("content"):
                        yield {"content": delta["content"]}
                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(tc.get("index", len(calls)), {"id": "", "name": "", "arguments": ""})
                        slot["id"] = tc.get("id") or slot["id"]
                        fn = tc.get("function") or {}
                        slot["name"] += fn.get("name") or ""
                        slot["arguments"] += fn.get("arguments") or ""
        if calls:
            result = []
            for slot in calls.values():
                try:
                    args = json.loads(slot["arguments"] or "{}")
                except ValueError:
                    args = {}
                result.append({"id": slot["id"], "function": {"name": slot["name"], "arguments": args}})
            yield {"tool_calls": result}


def make_llm(cfg):
    if cfg.get("backend") == "openai":
        return OpenAICompat(cfg["openai_base_url"], cfg["model"], cfg.get("openai_api_key", ""),
                            cfg["temperature"], cfg["num_ctx"])
    return Ollama(cfg["ollama_host"], cfg["model"], cfg["num_ctx"], cfg["temperature"], cfg["think"])

