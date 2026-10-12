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
from .llm import Ollama, OllamaError
from .tools import TOOLS, Context, execute, schemas, tool_list_text

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
- Schlägt etwas fehl, lies die Fehlermeldung und probiere einen anderen Weg.
- Erfinde nichts. Prüfe Fakten mit Werkzeugen (Dateien, Befehle, Websuche).
- Bei riskanten Aktionen (löschen, überschreiben, Systemeinstellungen) sei vorsichtig; ist der Wunsch \
unklar, frag kurz nach.
- Antworte knapp in der Sprache des Nutzers und fasse am Ende zusammen, was du getan hast.
- Merke dir dauerhaft wichtige Infos über den Nutzer mit dem Werkzeug `remember`.
{memory}{skills}{text_tools}"""

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
        sessions.append({"id": data["id"], "title": data.get("title") or "Neuer Chat", "updated": data.get("updated", 0)})
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
        if stripped.startswith("{") and stripped.endswith("}"):
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
        if role == "user" and content.startswith(RESULT_PREFIX) or role == "tool":
            for it in reversed(items):
                if it["type"] == "tool" and it.get("output") is None:
                    it["output"] = content.split("]\n", 1)[-1] if role == "user" else content
                    break
        elif role == "user":
            items.append({"type": "user", "text": content})
        elif role == "assistant":
            text = visible_text(content).strip()
            if text:
                items.append({"type": "assistant", "text": text})
            calls = m.get("tool_calls") or parse_text_tool_calls(content)
            for c in calls:
                items.append({"type": "tool", "name": c["function"]["name"], "args": c["function"]["arguments"], "output": None})
    return items


class Agent:
    def __init__(self, cfg, llm=None, session_id=None):
        self.cfg = cfg
        self.llm = llm or Ollama.from_config(cfg)
        self.session_id = session_id or new_id()
        self.ctx = Context(Path(cfg["workspace"]).expanduser(), config.HOME, cfg["command_timeout"])
        self.messages = []
        self.title = ""
        self.created = time.time()
        self.native_tools = True
        self.cancelled = False
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
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")

    # ------------------------------------------------------------ Prompt

    def system_prompt(self):
        memory_file = config.HOME / "memory.md"
        memory = memory_file.read_text("utf-8").strip() if memory_file.exists() else ""
        skills = []
        skills_dir = config.HOME / "skills"
        if skills_dir.is_dir():
            for f in sorted(skills_dir.glob("*.md")):
                first = f.read_text("utf-8").strip().splitlines()[:1]
                skills.append(f"- {f.stem}: {first[0].lstrip('# ') if first else ''} (Anleitung: {f})")
        return SYSTEM_PROMPT.format(
            os=f"{platform.system()} {platform.release()}",
            shell="PowerShell" if sys.platform == "win32" else "bash",
            py=platform.python_version(),
            now=datetime.now().strftime("%A, %d.%m.%Y %H:%M"),
            user_home=Path.home(),
            cwd=self.ctx.cwd,
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
        # Nie mitten in einer Werkzeugkette beginnen
        while start < len(self.messages) - 1 and (
            self.messages[start]["role"] != "user" or self.messages[start]["content"].startswith(RESULT_PREFIX)
        ):
            start += 1
        return [{"role": "system", "content": system}] + self.messages[start:]

    # ------------------------------------------------------------ Schleife

    def run(self, text, approve):
        """Generator mit Ereignissen. approve(request) -> bool wird bei gefährlichen Aktionen gerufen."""
        self.cancelled = False
        if hasattr(self.llm, "model"):
            self.llm.model = self.cfg["model"]
        if not self.title:
            self.title = text.strip().splitlines()[0][:60] if text.strip() else "Neuer Chat"
        self.messages.append({"role": "user", "content": text})
        try:
            for _ in range(self.cfg["max_steps"]):
                if self.cancelled:
                    yield {"type": "info", "text": "Gestoppt."}
                    break
                content, calls, shown = "", [], 0
                try:
                    for part in self.llm.chat(self._context_messages(), schemas() if self.native_tools else None):
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
                    if self.native_tools and "does not support tools" in str(e).lower():
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
                    output, ok = yield from self._run_tool(name, args, approve)
                    if self.native_tools:
                        self.messages.append({"role": "tool", "content": output, "tool_name": name})
                    else:
                        self.messages.append({"role": "user", "content": f"{RESULT_PREFIX}{name}]\n{output}"})
            else:
                yield {"type": "info", "text": f"Schrittlimit ({self.cfg['max_steps']}) erreicht – sag „weiter“, um fortzufahren."}
            yield {"type": "done"}
        finally:
            self.save()

    def _run_tool(self, name, args, approve):
        tool = TOOLS.get(name)
        yield {"type": "tool_call", "name": name, "args": args, "dangerous": bool(tool and tool.dangerous)}
        allowed = True
        if tool and tool.dangerous:
            mode = self.cfg["permission_mode"]
            if mode == "readonly":
                allowed = False
            elif mode == "ask":
                request = {"type": "approval", "id": uuid.uuid4().hex, "session": self.session_id, "name": name, "args": args}
                yield request
                allowed = bool(approve(request)) and not self.cancelled
        if allowed:
            ok, output = execute(self.ctx, name, args)
        else:
            ok, output = False, DENIED if self.cfg["permission_mode"] != "readonly" else \
                "Nur-Lesen-Modus aktiv: Diese Aktion ist nicht erlaubt."
        yield {"type": "tool_result", "name": name, "ok": ok, "output": output}
        return output, ok
