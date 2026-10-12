"""Lokaler Web-Server für die Oberfläche. Lauscht nur auf 127.0.0.1."""

import json
import mimetypes
import secrets
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config, scheduler
from .agent import Agent, delete_session, history_view, list_sessions, new_id
from .llm import OllamaError, make_llm
from .tools import NOTIFIERS

WEB = Path(__file__).parent / "web"
APPROVAL_TIMEOUT = 600
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.token = secrets.token_urlsafe(24)
        self.agents = {}
        self.busy = {}
        self.approvals = {}
        self.notices = []  # Benachrichtigungen für die Oberfläche
        self.lock = threading.Lock()
        NOTIFIERS.append(lambda title, text: self.notices.append(
            {"id": uuid.uuid4().hex, "title": title, "text": text, "time": time.time()}))

    def agent(self, session_id):
        with self.lock:
            if session_id not in self.agents:
                self.agents[session_id] = Agent(self.cfg, session_id=session_id)
                self.busy[session_id] = threading.Lock()
            return self.agents[session_id], self.busy[session_id]

    def status(self):
        llm = make_llm(self.cfg)
        info = {"backend": self.cfg["backend"], "model": self.cfg["model"],
                "permission_mode": self.cfg["permission_mode"], "workspace": self.cfg["workspace"],
                "vision": self.cfg["vision"], "can_pull": getattr(llm, "can_pull", False)}
        try:
            info["version"] = llm.version()
            info["models"] = llm.model_details()
            info["model_present"] = llm.has_model()
            info["capabilities"] = sorted(llm.capabilities())
            info["ok"] = True
        except OllamaError as e:
            info.update(ok=False, error=str(e), models=[], model_present=False, capabilities=[])
        return info

    def update_settings(self, body):
        for key in ("model", "backend", "permission_mode", "vision", "openai_base_url", "openai_api_key",
                    "ollama_host", "temperature", "num_ctx"):
            if key in body and body[key] != "":
                value = config.coerce(key, body[key]) if key in config.DEFAULTS else body[key]
                if key == "permission_mode" and value not in config.MODES:
                    continue
                if key == "backend" and value not in config.BACKENDS:
                    continue
                self.cfg[key] = value
        if body.get("workspace"):
            ws = Path(body["workspace"]).expanduser()
            if not ws.is_dir():
                raise ValueError(f"Ordner existiert nicht: {ws}")
            self.cfg["workspace"] = str(ws)
        self.cfg["ollama_host"] = config.normalize_host(self.cfg["ollama_host"])
        config.save(self.cfg)
        # offene Agenten sollen neues Arbeitsverzeichnis übernehmen (bei Modellwechsel bleibt der Chat)
        with self.lock:
            for a in self.agents.values():
                a.cfg = self.cfg

    def wait_for_approval(self, request, agent):
        event = threading.Event()
        self.approvals[request["id"]] = {"event": event, "decision": False}
        waited = 0.0
        while not event.wait(0.5):
            waited += 0.5
            if agent.cancelled or waited >= APPROVAL_TIMEOUT:
                break
        return self.approvals.pop(request["id"], {}).get("decision", False)

    def answer_approval(self, approval_id, decision):
        entry = self.approvals.get(approval_id)
        if entry:
            entry["decision"] = decision
            entry["event"].set()
        return bool(entry)


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Mirek"
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _allowed(self, api):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            if host not in ("127.0.0.1", "localhost", "[::1]", ""):
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
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

        def _emit(self, event):
            payload = (json.dumps(event, ensure_ascii=False) + "\n").encode()
            self.wfile.write(b"%X\r\n%s\r\n" % (len(payload), payload))
            self.wfile.flush()

        def _end_stream(self):
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

        # -------------------------------------------------- GET

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
            if url.path == "/api/tasks":
                return self._send(200, [{**t, "desc": scheduler.describe(t)} for t in scheduler.load_tasks()])
            if url.path == "/api/notices":
                notices, app.notices[:] = list(app.notices), []
                return self._send(200, notices)
            if url.path == "/api/image":
                return self._serve_image(query.get("path", [""])[0])
            self._send(404, {"error": "Nicht gefunden"})

        def _serve_image(self, path):
            p = Path(path)
            allowed_roots = [config.HOME, Path(app.cfg["workspace"]).expanduser(), Path.home()]
            try:
                p = p.resolve()
                if p.suffix.lower() not in IMAGE_TYPES or not p.is_file() \
                        or not any(str(p).startswith(str(r.resolve())) for r in allowed_roots):
                    return self._send(403, {"error": "verweigert"})
            except OSError:
                return self._send(404, {"error": "nicht gefunden"})
            self._send(200, p.read_bytes(), mimetypes.guess_type(str(p))[0] or "image/png")

        # -------------------------------------------------- POST

        def do_POST(self):
            if not self._allowed(True):
                return self._send(403, {"error": "Zugriff verweigert"})
            path = urlparse(self.path).path
            if path == "/api/upload":
                return self._upload()
            try:
                body = self._body()
            except ValueError:
                return self._send(400, {"error": "Ungültiges JSON"})
            if path == "/api/chat":
                return self._chat(body)
            if path == "/api/approve":
                return self._send(200, {"ok": app.answer_approval(body.get("id"), body.get("decision"))})
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
            if path == "/api/model/delete":
                try:
                    make_llm(app.cfg).delete(body.get("model", ""))
                except OllamaError as e:
                    return self._send(400, {"error": str(e)})
                return self._send(200, {"ok": True})
            if path == "/api/task/delete":
                scheduler.remove_task(body.get("id", ""))
                return self._send(200, {"ok": True})
            if path == "/api/task/toggle":
                scheduler.set_enabled(body.get("id", ""), body.get("enabled", True))
                return self._send(200, {"ok": True})
            self._send(404, {"error": "Nicht gefunden"})

        def _upload(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > 25_000_000:
                return self._send(413, {"error": "Datei zu groß (max 25 MB)"})
            data = self.rfile.read(length)
            name = Path(self.headers.get("X-Filename", "upload.bin")).name
            dest_dir = config.HOME / "uploads"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}-{name}"
            dest.write_bytes(data)
            is_image = dest.suffix.lower() in IMAGE_TYPES
            return self._send(200, {"path": str(dest), "name": name, "image": is_image})

        def _chat(self, body):
            text = str(body.get("text", "")).strip()
            images = [p for p in body.get("images", []) if Path(p).exists()]
            attachments = [p for p in body.get("files", []) if Path(p).exists()]
            if not text and not images and not attachments:
                return self._send(400, {"error": "Leere Nachricht"})
            if attachments:
                text += "\n\n[Angehängte Dateien:]\n" + "\n".join(attachments)
            agent, busy = app.agent(body.get("session") or new_id())
            if not busy.acquire(blocking=False):
                return self._send(409, {"error": "Mirek arbeitet in diesem Chat noch."})
            events = agent.run(text, lambda req: app.wait_for_approval(req, agent), images=images)
            try:
                self._start_stream()
                for event in events:
                    self._emit(event)
                self._end_stream()
            except (BrokenPipeError, ConnectionResetError):
                agent.cancelled = True
            finally:
                events.close()
                busy.release()

        def _pull(self, model):
            self._start_stream()
            try:
                for progress in make_llm(app.cfg).pull(model):
                    self._emit(progress)
                self._emit({"status": "fertig", "done": True})
            except OllamaError as e:
                self._emit({"error": str(e)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                try:
                    self._end_stream()
                except OSError:
                    pass

    return Handler


def serve(cfg, open_browser=True):
    app = App(cfg)
    sched = scheduler.Scheduler(cfg)
    sched.start()
    server = ThreadingHTTPServer(("127.0.0.1", cfg["port"]), make_handler(app))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{cfg['port']}"
    print(f"\n  ⚡ Mirek läuft auf {url}\n  (Beenden mit Strg+C)\n")
    if open_browser:
        threading.Timer(0.6, _open_ui, [url, cfg]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTschüss! 👋")
    finally:
        sched.stop_event.set()
        server.server_close()


def _open_ui(url, cfg):
    """Öffnet Mirek möglichst als eigenes App-Fenster (Chrome/Edge --app), sonst im Browser."""
    import shutil
    import subprocess

    if cfg.get("app_window"):
        for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
            exe = shutil.which(name)
            if exe:
                try:
                    subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={config.HOME / 'browser'}"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except OSError:
                    break
    webbrowser.open(url)
