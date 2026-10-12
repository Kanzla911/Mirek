"""Werkzeug-Register. Jedes Werkzeug ist eine Funktion mit @tool – auch Plugins in ~/.mirek/plugins/."""

import importlib.util
import os
import re
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

MAX_OUTPUT = 12000


@dataclass
class Context:
    cwd: Path
    home: Path
    command_timeout: int = 120
    session_id: str = ""
    cfg: dict = field(default_factory=dict)
    vision: bool = False
    events: list = field(default_factory=list)  # Ereignisse für die Oberfläche (Plan, Hinweise …)

    def emit(self, event):
        self.events.append(event)


@dataclass
class Result:
    """Rückgabe eines Werkzeugs mit Bildern (z.B. Screenshot) zusätzlich zum Text."""

    text: str
    images: list = field(default_factory=list)


@dataclass
class Tool:
    name: str
    description: str
    params: dict
    required: list
    dangerous: object  # bool oder Funktion(args) -> bool
    fn: object = field(repr=False)
    plugin: bool = False

    def schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": self.params, "required": self.required},
            },
        }

    def is_dangerous(self, args):
        return bool(self.dangerous(args) if callable(self.dangerous) else self.dangerous)


TOOLS = {}
NOTIFIERS = []  # Funktionen (titel, text), die Benachrichtigungen weiterleiten (Telegram, Oberfläche …)
_loading_plugin = False


def tool(name, description, params=None, required=(), dangerous=False):
    def deco(fn):
        TOOLS[name] = Tool(name, description, params or {}, list(required), dangerous, fn, _loading_plugin)
        return fn

    return deco


def schemas():
    return [t.schema() for t in TOOLS.values()]


def clip(text, limit=MAX_OUTPUT):
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n…[{len(text) - limit} Zeichen gekürzt]…\n{text[-half:]}"


def resolve(ctx, path):
    p = Path(os.path.expanduser(str(path or ".")))
    if not p.is_absolute():
        p = ctx.cwd / p
    return p.resolve()


def as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_bool(value):
    return value is True or str(value).lower() in ("true", "1", "yes", "ja")


# Befehle, die selbst im Modus „Volle Macht“ immer nachfragen
CRITICAL = re.compile(
    r"rm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME|/\*|~/\*)(\s|$)|rm\s+-[a-z]*f[a-z]*r\s+(/|~)(\s|$)"
    r"|\bmkfs\b|\bdd\b[^|]*\bof=/dev/|>\s*/dev/sd|\bformat\s+[a-z]:|\bdiskpart\b"
    r"|Remove-Item\s+.*-Recurse.*\s[A-Z]:\\\s*($|-)|:\(\)\s*\{\s*:\|:&\s*\};:"
    r"|\bshutdown\b|\breboot\b|Stop-Computer|Restart-Computer|\bchmod\s+-R\s+\d+\s+/(\s|$)",
    re.I,
)


def is_critical(name, args):
    if name not in ("run_command", "process"):
        return False
    return bool(CRITICAL.search(str(args.get("command", ""))))


def execute(ctx, name, args):
    """Führt ein Werkzeug aus. Gibt (erfolgreich, text, bilder) zurück."""
    t = TOOLS.get(name)
    if t is None:
        return False, f"Unbekanntes Werkzeug '{name}'. Verfügbar: {', '.join(TOOLS)}", []
    if not isinstance(args, dict):
        return False, "Die Argumente müssen ein JSON-Objekt sein.", []
    args = {k: v for k, v in args.items() if k in t.params}
    missing = [r for r in t.required if args.get(r) in (None, "")]
    if missing:
        return False, f"Fehlende Argumente für {name}: {', '.join(missing)}", []
    try:
        out = t.fn(ctx, **args)
    except Exception as e:  # Fehler gehen als Text an das Modell zurück, damit es reagieren kann
        return False, f"Fehler: {type(e).__name__}: {e}", []
    if isinstance(out, Result):
        return True, clip(out.text), [str(p) for p in out.images]
    return True, clip(str(out)), []


def tool_list_text():
    """Werkzeugbeschreibung für Modelle ohne native Tool-Unterstützung."""
    lines = []
    for t in TOOLS.values():
        params = ", ".join(f"{k}{'' if k in t.required else '?'}: {v.get('type', 'string')}" for k, v in t.params.items())
        lines.append(f"- {t.name}({params}): {t.description}")
    return "\n".join(lines)


def notify_all(title, text):
    for fn in list(NOTIFIERS):
        try:
            fn(title, text)
        except Exception:  # eine kaputte Weiterleitung darf nichts anderes stören
            traceback.print_exc()


def load_plugins(home):
    """Lädt ~/.mirek/plugins/*.py neu. Gibt Liste (datei, fehler|None) zurück."""
    global _loading_plugin
    for name in [n for n, t in TOOLS.items() if t.plugin]:
        del TOOLS[name]
    results = []
    plugin_dir = Path(home) / "plugins"
    if not plugin_dir.is_dir():
        return results
    for path in sorted(plugin_dir.glob("*.py")):
        _loading_plugin = True
        try:
            spec = importlib.util.spec_from_file_location(f"mirek_plugin_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            results.append((path.name, None))
        except Exception as e:
            results.append((path.name, f"{type(e).__name__}: {e}"))
        finally:
            _loading_plugin = False
    return results


# Werkzeuge registrieren
from . import files, system, web, docs, desktop, organize  # noqa: E402,F401
