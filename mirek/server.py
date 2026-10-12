"""Lokaler Web-Server für die Oberfläche. Lauscht nur auf 127.0.0.1."""

import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config
from .agent import Agent, delete_session, history_view, list_sessions, new_id
from .llm import Ollama, OllamaError

WEB = Path(__file__).parent / "web"
APPROVAL_TIMEOUT = 600


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.token = secrets.token_urlsafe(24)
        self.agents = {}
        self.busy = {}
        self.approvals = {}
        self.lock = threading.Lock()

    def agent(self, session_id):
        with self.lock:
            if session_id not in self.agents:
                self.agents[session_id] = Agent(self.cfg, session_id=session_id)
                self.busy[session_id] = threading.Lock()
            return self.agents[session_id], self.busy[session_id]

    def status(self):
        llm = Ollama.from_config(self.cfg)
        info = {"model": self.cfg["model"], "permission_mode": self.cfg["permission_mode"],
                "workspace": self.cfg["workspace"], "host": self.cfg["ollama_host"]}
        try:
            info.update(ok=True, version=llm.version(), models=llm.models())
            info["model_present"] = llm.has_model()
        except OllamaError as e:
            info.update(ok=False, error=str(e), models=[], model_present=False)
        return info

    def update_settings(self, body):
        if "model" in body and str(body["model"]).strip():
            self.cfg["model"] = str(body["model"]).strip()
        if body.get("permission_mode") in config.MODES:
            self.cfg["permission_mode"] = body["permission_mode"]
        if body.get("workspace"):
            ws = Path(body["workspace"]).expanduser()
            if not ws.is_dir():
                raise ValueError(f"Ordner existiert nicht: {ws}")
            self.cfg["workspace"] = str(ws)
        config.save(self.cfg)

    def wait_for_approval(self, request, agent):
        event = threading.Event()
        self.approvals[request["id"]] = {"event": event, "allow": False, "session": request["session"]}
        waited = 0.0
        while not event.wait(0.5):
            waited += 0.5
            if agent.cancelled or waited >= APPROVAL_TIMEOUT:
                break
        return self.approvals.pop(request["id"])["allow"]

    def answer_approval(self, approval_id, allow):
        entry = self.approvals.get(approval_id)
        if entry:
            entry["allow"] = bool(allow)
            entry["event"].set()
        return bool(entry)


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Mirek"

        def log_message(self, *args):
            pass

        # -------------------------------------------------- Hilfen

        def _allowed(self, api):
            # Schutz vor DNS-Rebinding und fremden Webseiten: nur localhost + geheimer Token
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            if host not in ("127.0.0.1", "localhost", "[::1]"):
                return False
            return not api or secrets.compare_digest(self.headers.get("X-Mirek-Token", ""), app.token)

        def _send(self, status, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def _start_stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

        def _emit(self, event):
            self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode())
            self.wfile.flush()

        # -------------------------------------------------- Routen

        def do_GET(self):
            url = urlparse(self.path)
            api = url.path.startswith("/api/")
            if not self._allowed(api):
                return self._send(403, {"error": "Zugriff verweigert"})
            query = parse_qs(url.query)
            if url.path == "/":
                page = (WEB / "index.html").read_text("utf-8").replace("__MIREK_TOKEN__", app.token)
                return self._send(200, page.encode(), "text/html; charset=utf-8")
            if url.path == "/api/status":
                return self._send(200, app.status())
            if url.path == "/api/sessions":
                return self._send(200, list_sessions())
            if url.path == "/api/session":
                agent, busy = app.agent(query.get("id", [new_id()])[0])
                return self._send(200, {"id": agent.session_id, "title": agent.title, "cwd": str(agent.ctx.cwd),
                                        "busy": busy.locked(), "items": history_view(agent.messages)})
            self._send(404, {"error": "Nicht gefunden"})

        def do_POST(self):
            if not self._allowed(True):
                return self._send(403, {"error": "Zugriff verweigert"})
            try:
                body = self._body()
            except ValueError:
                return self._send(400, {"error": "Ungültiges JSON"})
            path = urlparse(self.path).path
            if path == "/api/chat":
                return self._chat(body)
            if path == "/api/approve":
                return self._send(200, {"ok": app.answer_approval(body.get("id"), body.get("allow"))})
            if path == "/api/stop":
                agent, _ = app.agent(body.get("session", ""))
                agent.cancelled = True
                return self._send(200, {"ok": True})
            if path == "/api/session/new":
                return self._send(200, {"id": new_id()})
            if path == "/api/session/delete":
                sid = body.get("id", "")
                with app.lock:
                    app.agents.pop(sid, None)
                delete_session(sid)
                return self._send(200, {"ok": True})
            if path == "/api/settings":
                try:
                    app.update_settings(body)
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                return self._send(200, app.status())
            if path == "/api/pull":
                return self._pull(body.get("model") or app.cfg["model"])
            self._send(404, {"error": "Nicht gefunden"})

        def _chat(self, body):
            text = str(body.get("text", "")).strip()
            if not text:
                return self._send(400, {"error": "Leere Nachricht"})
            agent, busy = app.agent(body.get("session") or new_id())
            if not busy.acquire(blocking=False):
                return self._send(409, {"error": "Mirek arbeitet in diesem Chat noch."})
            events = agent.run(text, lambda req: app.wait_for_approval(req, agent))
            try:
                self._start_stream()
                for event in events:
                    self._emit(event)
            except (BrokenPipeError, ConnectionResetError):
                agent.cancelled = True
            finally:
                events.close()
                busy.release()

        def _pull(self, model):
            self._start_stream()
            try:
                for progress in Ollama.from_config(app.cfg).pull(model):
                    self._emit(progress)
                self._emit({"status": "fertig", "done": True})
            except OllamaError as e:
                self._emit({"error": str(e)})
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


def serve(cfg, open_browser=True):
    app = App(cfg)
    server = ThreadingHTTPServer(("127.0.0.1", cfg["port"]), make_handler(app))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{cfg['port']}"
    print(f"\n  ⚡ Mirek läuft auf {url}\n  (Beenden mit Strg+C)\n")
    if open_browser:
        threading.Timer(0.6, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTschüss! 👋")
    finally:
        server.server_close()
