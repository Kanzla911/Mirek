"""Die Agenten-Schleife: Modell denkt → benutzt Werkzeuge → sieht Ergebnis → … → antwortet."""

import json
import platform
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import config
from .llm import OllamaError, make_llm
from .tools import TOOLS, Context, execute, is_critical, load_plugins, schemas, tool_list_text

TOOL_TAG = re.compile(r"<tool(?:_call)?>\s*(\{.*?\})\s*</tool(?:_call)?>", re.S)
RESULT_PREFIX = "[Ergebnis von "
DENIED = "Der Nutzer hat diese Aktion abgelehnt. Frage nach oder versuche einen anderen Weg."

SYSTEM_PROMPT = """Du bist Mirek – „Mirek die Macht alles“: ein persönlicher KI-Assistent, der lokal auf dem \
Computer des Nutzers läuft und mit Werkzeugen echte Aktionen ausführt.

System: {os} | Shell: {shell} | Python {py} | Jetzt: {now}
Benutzerordner: {user_home}
Aktuelles Arbeitsverzeichnis: {cwd}

So arbeitest du:
- Erledige Aufgaben selbst mit deinen Werkzeugen, statt nur zu erklären, wie es geht.
- Behaupte NIE, etwas getan zu haben (Datei geschrieben, Befehl ausgeführt …), ohne dafür wirklich ein \
Werkzeug aufgerufen zu haben.
- Geh Schritt für Schritt vor: erst nachsehen (Ordner auflisten, Datei lesen, Befehl prüfen), dann handeln, \
dann das Ergebnis kontrollieren.
- Bei Aufgaben mit mehreren Schritten: lege zuerst mit `update_plan` eine Checkliste an und halte sie aktuell.
- Schlägt etwas fehl, lies die Fehlermeldung und probiere einen anderen Weg. Fehlt ein Programm oder \
Python-Paket, installiere es (z.B. mit `run_command` und pip/dem Paketmanager) statt aufzugeben.
- Erfinde nichts. Prüfe Fakten mit Werkzeugen (Dateien, Befehle, Websuche).
- Du veränderst Dateien gefahrlos: write_file/edit_file/delete_file legen Sicherungen an, `undo_file` macht \
Änderungen rückgängig. Gelöschtes landet im Mirek-Papierkorb.
- Bei unklaren oder sehr weitreichenden Wünschen frag kurz nach, bevor du große Änderungen machst.
- Antworte knapp in der Sprache des Nutzers und fasse am Ende zusammen, was du getan hast.
- Merke dir dauerhaft Wichtiges über den Nutzer mit `remember`. Für „später“/„jeden Tag“ nutze `schedule`.
{vision}{memory}{skills}{text_tools}"""

TEXT_TOOLS = """
Werkzeuge:
{tools}

Um ein Werkzeug zu benutzen, antworte NUR mit einem Block in genau diesem Format (keine weiteren Worte):
<tool>{{"name": "werkzeugname", "arguments": {{"parameter": "wert"}}}}</tool>
Danach bekommst du das Ergebnis und kannst weitermachen. Wenn du fertig bist, antworte normal ohne <tool>.
"""


def new_id():
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def chats_dir():
    d = config.HOME / "chats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_sessions():
    sessions = []
    for f in chats_dir().glob("*.json"):
        try:
            data = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("messages"):
            sessions.append({"id": data["id"], "title": data.get("title") or "Neuer Chat",
                             "updated": data.get("updated", 0)})
    return sorted(sessions, key=lambda s: s["updated"], reverse=True)


def delete_session(session_id):
    path = chats_dir() / f"{Path(session_id).name}.json"
    if path.exists():
        path.unlink()


def visible_text(content):
    """Text ohne Werkzeug-Markup – hält auch ein halbes '<tool' am Ende zurück."""
    idx = content.find("<tool")
    if idx >= 0:
        return content[:idx]
    for k in range(min(len(content), 10), 0, -1):
        if "<tool_call>".startswith(content[-k:]):
            return content[:-k]
    return content


def parse_text_tool_calls(content):
    """Findet Werkzeugaufrufe, die ein Modell als Text statt nativ geschrieben hat."""
    candidates = TOOL_TAG.findall(content)
    if not candidates:
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        if stripped.startswith("{") and stripped.endswith("}") and '"name"' in stripped:
            candidates = [stripped]
    calls = []
    for raw in candidates:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        name = data.get("name") if isinstance(data, dict) else None
        if name in TOOLS:
            args = data.get("arguments", data.get("parameters", {}))
            calls.append({"function": {"name": name, "arguments": args}})
    return calls


def history_view(messages):
    """Wandelt den gespeicherten Verlauf in Anzeige-Elemente für die Oberfläche um."""
    items = []
    for m in messages:
        role, content = m["role"], m.get("content") or ""
        if role == "tool" or (role == "user" and content.startswith(RESULT_PREFIX)):
            text = content.split("]\n", 1)[-1] if role == "user" else content
            for it in reversed(items):
                if it["type"] == "tool" and it.get("output") is None:
                    it["output"] = text
                    it["images"] = m.get("image_paths", [])
                    break
        elif role == "user":
            items.append({"type": "user", "text": content, "images": m.get("image_paths", [])})
        elif role == "assistant":
            text = visible_text(content).strip()
            if text:
                items.append({"type": "assistant", "text": text})
            for c in m.get("tool_calls") or parse_text_tool_calls(content):
                items.append({"type": "tool", "name": c["function"]["name"],
                              "args": c["function"]["arguments"], "output": None, "images": []})
    return items


class Agent:
    def __init__(self, cfg, llm=None, session_id=None):
        self.cfg = cfg
        self.llm = llm or make_llm(cfg)
        self.session_id = session_id or new_id()
        self.ctx = Context(Path(cfg["workspace"]).expanduser(), config.HOME,
                           cfg["command_timeout"], self.session_id, cfg)
        self.messages = []
        self.title = ""
        self.created = time.time()
        self.native_tools = True
        self.cancelled = False
        self.always_allow = set()
        self._plugins_loaded = False
        self._load()

    # ------------------------------------------------------------ Speicher

    @property
    def path(self):
        return chats_dir() / f"{Path(self.session_id).name}.json"

    def _load(self):
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text("utf-8"))
        self.messages = data.get("messages", [])
        self.title = data.get("title", "")
        self.created = data.get("created", self.created)
        self.native_tools = data.get("native_tools", True)
        cwd = Path(data.get("cwd", ""))
        if data.get("cwd") and cwd.is_dir():
            self.ctx.cwd = cwd

    def save(self):
        data = {
            "id": self.session_id, "title": self.title, "created": self.created, "updated": time.time(),
            "cwd": str(self.ctx.cwd), "native_tools": self.native_tools, "messages": self.messages,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------ Prompt

    def detect_vision(self):
        setting = self.cfg.get("vision", "auto")
        if setting in (True, False):
            return setting
        try:
            return "vision" in self.llm.capabilities()
        except Exception:
            return False

    def system_prompt(self):
        memory_file = config.HOME / "memory.md"
        memory = memory_file.read_text("utf-8").strip() if memory_file.exists() else ""
        skills = []
        skills_dir = config.HOME / "skills"
        if skills_dir.is_dir():
            for f in sorted(skills_dir.glob("*.md")):
                head = f.read_text("utf-8").strip().splitlines()[:1]
                skills.append(f"- {f.stem}: {head[0].lstrip('# ') if head else ''} (Anleitung: {f})")
        return SYSTEM_PROMPT.format(
            os=f"{platform.system()} {platform.release()}",
            shell="PowerShell" if sys.platform == "win32" else "bash",
            py=platform.python_version(),
            now=datetime.now().strftime("%A, %d.%m.%Y %H:%M"),
            user_home=Path.home(),
            cwd=self.ctx.cwd,
            vision=("Du kannst Bilder sehen: Screenshots und Bilddateien werden dir direkt gezeigt.\n"
                    if self.ctx.vision else ""),
            memory=f"\nDein Gedächtnis über den Nutzer:\n{memory}\n" if memory else "",
            skills=("\nSkills (bei passender Aufgabe zuerst die Anleitung mit read_file lesen):\n"
                    + "\n".join(skills) + "\n") if skills else "",
            text_tools="" if self.native_tools else TEXT_TOOLS.format(tools=tool_list_text()),
        )

    def _context_messages(self):
        system = self.system_prompt()
        budget = self.cfg["num_ctx"] * 3 - len(system)  # grob: ~3 Zeichen pro Token
        used, start = 0, len(self.messages)
        for i in range(len(self.messages) - 1, -1, -1):
            used += len(json.dumps(self.messages[i], ensure_ascii=False))
            if used > budget and start < len(self.messages):
                break
            start = i
        while start < len(self.messages) - 1 and (
            self.messages[start]["role"] != "user" or self.messages[start]["content"].startswith(RESULT_PREFIX)
        ):
            start += 1
        return [{"role": "system", "content": system}] + self.messages[start:]

    # ------------------------------------------------------------ Schleife

    def run(self, text, approve, images=None):
        """Generator mit Ereignissen. approve(request)->bool wird bei gefährlichen Aktionen gerufen."""
        self.cancelled = False
        self.llm.model = self.cfg["model"]
        self.ctx.cfg = self.cfg
        self.ctx.command_timeout = self.cfg["command_timeout"]
        self.ctx.vision = self.detect_vision()
        if not self._plugins_loaded:
            load_plugins(config.HOME)
            self._plugins_loaded = True
        if not self.title:
            self.title = text.strip().splitlines()[0][:60] if text.strip() else "Neuer Chat"
        user_msg = {"role": "user", "content": text}
        if images:
            user_msg["image_paths"] = list(images)
        self.messages.append(user_msg)
        try:
            for _ in range(self.cfg["max_steps"]):
                if self.cancelled:
                    yield {"type": "info", "text": "Gestoppt."}
                    break
                content, calls, shown = "", [], 0
                try:
                    for part in self.llm.chat(self._context_messages(), schemas() if self.native_tools else None,
                                              vision=self.ctx.vision):
                        if self.cancelled:
                            break
                        if part.get("thinking"):
                            yield {"type": "thinking", "text": part["thinking"]}
                        if part.get("content"):
                            content += part["content"]
                            visible = visible_text(content)
                            if len(visible) > shown:
                                yield {"type": "token", "text": visible[shown:]}
                                shown = len(visible)
                        if part.get("tool_calls"):
                            calls.extend(part["tool_calls"])
                except OllamaError as e:
                    if self.native_tools and self._looks_like_no_tools(e):
                        self.native_tools = False
                        yield {"type": "info", "text": "Dieses Modell kann keine nativen Werkzeuge – ich nutze den Text-Modus."}
                        continue
                    yield {"type": "error", "text": str(e)}
                    break

                if not calls:
                    calls = parse_text_tool_calls(content)
                message = {"role": "assistant", "content": content}
                if calls and self.native_tools:
                    message["tool_calls"] = calls
                self.messages.append(message)
                if not calls or self.cancelled:
                    if self.cancelled:
                        yield {"type": "info", "text": "Gestoppt."}
                    break

                for call in calls:
                    fn = call.get("function", {})
                    name, args = fn.get("name", ""), fn.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except ValueError:
                            args = {}
                    yield from self._run_tool(call, name, args, approve)
            else:
                yield {"type": "info", "text": f"Schrittlimit ({self.cfg['max_steps']}) erreicht – sag „weiter“, um fortzufahren."}
            yield {"type": "done"}
        finally:
            self.save()

    @staticmethod
    def _looks_like_no_tools(error):
        msg = str(error).lower()
        return "does not support tools" in msg or "tools" in msg and "support" in msg

    def _run_tool(self, call, name, args, approve):
        tool = TOOLS.get(name)
        yield {"type": "tool_call", "name": name, "args": args, "dangerous": bool(tool and tool.is_dangerous(args))}
        self.ctx.events.clear()
        allowed = True
        if tool and tool.is_dangerous(args):
            mode = self.cfg["permission_mode"]
            critical = is_critical(name, args)
            if mode == "readonly":
                allowed = False
            elif mode == "auto" and not critical:
                allowed = True
            elif name in self.always_allow and not critical:
                allowed = True
            else:
                request = {"type": "approval", "id": uuid.uuid4().hex, "session": self.session_id,
                           "name": name, "args": args, "critical": critical}
                yield request
                decision = approve(request)
                allowed = bool(decision) and not self.cancelled
                if decision == "always" and not critical:
                    self.always_allow.add(name)

        images = []
        if allowed:
            ok, output, images = execute(self.ctx, name, args)
        elif self.cfg["permission_mode"] == "readonly":
            ok, output = False, "Nur-Lesen-Modus aktiv: Diese verändernde Aktion ist nicht erlaubt."
        else:
            ok, output = False, DENIED

        for ev in self.ctx.events:
            yield ev
        self.ctx.events.clear()

        tool_msg = {"role": "tool", "content": output, "tool_name": name,
                    "tool_call_id": call.get("id", "")}
        if images:
            tool_msg["image_paths"] = images
        if self.native_tools:
            self.messages.append(tool_msg)
        else:
            text_msg = {"role": "user", "content": f"{RESULT_PREFIX}{name}]\n{output}"}
            if images:
                text_msg["image_paths"] = images
            self.messages.append(text_msg)
        yield {"type": "tool_result", "name": name, "ok": ok, "output": output, "images": images}
