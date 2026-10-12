"""Die Werkzeuge, mit denen Mirek echte Dinge auf dem Computer erledigt."""

import fnmatch
import html
import os
import platform
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

MAX_OUTPUT = 12000
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache", "AppData", "Library"}


@dataclass
class Context:
    cwd: Path
    home: Path
    command_timeout: int = 120


@dataclass
class Tool:
    name: str
    description: str
    params: dict
    required: list
    dangerous: bool
    fn: object = field(repr=False)

    def schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": self.params, "required": self.required},
            },
        }


TOOLS = {}


def tool(name, description, params=None, required=(), dangerous=False):
    def deco(fn):
        TOOLS[name] = Tool(name, description, params or {}, list(required), dangerous, fn)
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


def execute(ctx, name, args):
    """Führt ein Werkzeug aus. Gibt (erfolgreich, ausgabe) zurück."""
    t = TOOLS.get(name)
    if t is None:
        return False, f"Unbekanntes Werkzeug '{name}'. Verfügbar: {', '.join(TOOLS)}"
    if not isinstance(args, dict):
        return False, "Die Argumente müssen ein JSON-Objekt sein."
    args = {k: v for k, v in args.items() if k in t.params}
    missing = [r for r in t.required if args.get(r) in (None, "")]
    if missing:
        return False, f"Fehlende Argumente für {name}: {', '.join(missing)}"
    try:
        return True, clip(str(t.fn(ctx, **args)))
    except Exception as e:  # Fehler gehen als Text an das Modell zurück, damit es reagieren kann
        return False, f"Fehler: {type(e).__name__}: {e}"


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value):
    return value is True or str(value).lower() in ("true", "1", "yes", "ja")


# ---------------------------------------------------------------- System


@tool(
    "run_command",
    "Führt einen Shell-Befehl aus (Linux/macOS: bash, Windows: PowerShell) und liefert die Ausgabe. "
    "Für Programme, die weiterlaufen sollen (Server, GUI-Apps), background=true setzen.",
    {
        "command": {"type": "string", "description": "Der auszuführende Befehl"},
        "timeout": {"type": "integer", "description": "Zeitlimit in Sekunden (Standard 120)"},
        "background": {"type": "boolean", "description": "Im Hintergrund starten, nicht auf Ende warten"},
    },
    ["command"],
    dangerous=True,
)
def run_command(ctx, command, timeout=None, background=False):
    if os.name == "nt":
        args, shell = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command], False
    else:
        args, shell = command, True
    if _bool(background):
        kwargs = {"start_new_session": True} if os.name != "nt" else {"creationflags": 0x00000008}
        p = subprocess.Popen(
            args, shell=shell, cwd=ctx.cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs,
        )
        return f"Im Hintergrund gestartet (PID {p.pid})."
    limit = _int(timeout, ctx.command_timeout)
    try:
        p = subprocess.run(
            args, shell=shell, cwd=ctx.cwd, capture_output=True, text=True, timeout=limit,
            stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
            executable="/bin/bash" if shell and Path("/bin/bash").exists() else None,
        )
    except subprocess.TimeoutExpired as e:
        partial = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return f"Zeitüberschreitung nach {limit}s. Bisherige Ausgabe:\n{partial}"
    out = f"Exit-Code: {p.returncode}\n"
    if p.stdout:
        out += p.stdout
    if p.stderr:
        out += f"\n[stderr]\n{p.stderr}"
    if not p.stdout and not p.stderr:
        out += "(keine Ausgabe)"
    return out


@tool(
    "run_python",
    "Führt Python-Code aus (z.B. für Berechnungen, Datenverarbeitung, Excel/CSV, Bilder). Ausgabe per print().",
    {
        "code": {"type": "string", "description": "Python-Code"},
        "timeout": {"type": "integer", "description": "Zeitlimit in Sekunden"},
    },
    ["code"],
    dangerous=True,
)
def run_python(ctx, code, timeout=None):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        script = f.name
    try:
        p = subprocess.run(
            [sys.executable, script], cwd=ctx.cwd, capture_output=True, text=True,
            timeout=_int(timeout, ctx.command_timeout), stdin=subprocess.DEVNULL,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return "Zeitüberschreitung."
    finally:
        os.unlink(script)
    out = p.stdout + (f"\n[stderr]\n{p.stderr}" if p.stderr else "")
    return f"Exit-Code: {p.returncode}\n{out or '(keine Ausgabe)'}"


@tool(
    "open",
    "Öffnet eine Datei, einen Ordner, eine Webseite (URL) oder ein Programm auf dem Computer des Nutzers.",
    {"target": {"type": "string", "description": "Pfad, URL oder Programmname"}},
    ["target"],
    dangerous=True,
)
def open_target(ctx, target):
    if re.match(r"^[a-z][a-z0-9+.-]*://", target, re.I):
        webbrowser.open(target)
        return f"Geöffnet: {target}"
    path = resolve(ctx, target)
    system = platform.system()
    if path.exists():
        if system == "Windows":
            os.startfile(str(path))  # noqa: S606
        else:
            subprocess.Popen(["open" if system == "Darwin" else "xdg-open", str(path)], start_new_session=True)
        return f"Geöffnet: {path}"
    # Kein Pfad → als Programmname behandeln
    if system == "Windows":
        subprocess.Popen(["cmd", "/c", "start", "", target])
    elif system == "Darwin":
        subprocess.Popen(["open", "-a", target], start_new_session=True)
    else:
        subprocess.Popen([target], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"Programm gestartet: {target}"


@tool(
    "change_directory",
    "Wechselt das aktuelle Arbeitsverzeichnis für alle folgenden Befehle und relativen Pfade.",
    {"path": {"type": "string", "description": "Zielordner"}},
    ["path"],
)
def change_directory(ctx, path):
    p = resolve(ctx, path)
    if not p.is_dir():
        raise NotADirectoryError(f"{p} ist kein Ordner")
    ctx.cwd = p
    return f"Arbeitsverzeichnis ist jetzt {p}"


# ---------------------------------------------------------------- Dateien


@tool(
    "read_file",
    "Liest eine Textdatei.",
    {
        "path": {"type": "string", "description": "Dateipfad"},
        "start_line": {"type": "integer", "description": "Erste Zeile (ab 1)"},
        "max_lines": {"type": "integer", "description": "Maximale Anzahl Zeilen (Standard 400)"},
    },
    ["path"],
)
def read_file(ctx, path, start_line=1, max_lines=400):
    p = resolve(ctx, path)
    raw = p.read_bytes()
    if b"\0" in raw[:2048]:
        return f"{p} ist eine Binärdatei ({len(raw)} Bytes)."
    lines = raw.decode("utf-8", "replace").splitlines()
    start = max(_int(start_line, 1), 1)
    end = min(start - 1 + _int(max_lines, 400), len(lines))
    header = f"[{p} – Zeilen {start}-{end} von {len(lines)}]\n"
    return header + "\n".join(lines[start - 1:end])


@tool(
    "write_file",
    "Schreibt Text in eine Datei (legt Ordner automatisch an). Überschreibt, außer append=true.",
    {
        "path": {"type": "string", "description": "Dateipfad"},
        "content": {"type": "string", "description": "Inhalt"},
        "append": {"type": "boolean", "description": "Anhängen statt überschreiben"},
    },
    ["path", "content"],
    dangerous=True,
)
def write_file(ctx, path, content, append=False):
    p = resolve(ctx, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if _bool(append) else "w", encoding="utf-8") as f:
        f.write(content)
    return f"Gespeichert: {p} ({len(content)} Zeichen)"


@tool(
    "edit_file",
    "Ersetzt einen exakten Textabschnitt in einer Datei. old_text muss genau einmal vorkommen "
    "(oder replace_all=true).",
    {
        "path": {"type": "string", "description": "Dateipfad"},
        "old_text": {"type": "string", "description": "Exakter alter Text"},
        "new_text": {"type": "string", "description": "Neuer Text"},
        "replace_all": {"type": "boolean", "description": "Alle Vorkommen ersetzen"},
    },
    ["path", "old_text"],
    dangerous=True,
)
def edit_file(ctx, path, old_text, new_text="", replace_all=False):
    p = resolve(ctx, path)
    text = p.read_text("utf-8")
    count = text.count(old_text)
    if count == 0:
        raise ValueError("old_text wurde nicht gefunden – lies die Datei zuerst mit read_file.")
    if count > 1 and not _bool(replace_all):
        raise ValueError(f"old_text kommt {count}× vor – mehr Kontext angeben oder replace_all=true.")
    p.write_text(text.replace(old_text, new_text), "utf-8")
    return f"{p}: {count if _bool(replace_all) else 1} Stelle(n) ersetzt."


@tool(
    "list_dir",
    "Listet den Inhalt eines Ordners auf.",
    {"path": {"type": "string", "description": "Ordner (Standard: aktuelles Verzeichnis)"}},
)
def list_dir(ctx, path="."):
    p = resolve(ctx, path)
    entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    lines = [f"[{p}] {len(entries)} Einträge"]
    for e in entries[:300]:
        try:
            lines.append(f"{e.name}/" if e.is_dir() else f"{e.name}  ({_size(e.stat().st_size)})")
        except OSError:
            lines.append(e.name)
    if len(entries) > 300:
        lines.append(f"… und {len(entries) - 300} weitere")
    return "\n".join(lines)


def _size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _walk(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            yield Path(dirpath) / name


@tool(
    "find_files",
    "Sucht Dateien nach Namensmuster (z.B. '*.pdf', 'rechnung*') rekursiv.",
    {
        "pattern": {"type": "string", "description": "Glob-Muster"},
        "path": {"type": "string", "description": "Startordner"},
    },
    ["pattern"],
)
def find_files(ctx, pattern, path="."):
    root = resolve(ctx, path)
    pattern = pattern.lower()
    hits = []
    for f in _walk(root):
        if fnmatch.fnmatch(f.name.lower(), pattern):
            hits.append(str(f))
            if len(hits) >= 200:
                hits.append("… (abgeschnitten nach 200 Treffern)")
                break
    return "\n".join(hits) or "Keine Treffer."


@tool(
    "search_text",
    "Durchsucht Textdateien nach einem Begriff (ohne Groß-/Kleinschreibung).",
    {
        "query": {"type": "string", "description": "Suchbegriff"},
        "path": {"type": "string", "description": "Startordner oder Datei"},
        "file_pattern": {"type": "string", "description": "Nur Dateien wie z.B. '*.py'"},
    },
    ["query"],
)
def search_text(ctx, query, path=".", file_pattern="*"):
    root = resolve(ctx, path)
    files = [root] if root.is_file() else _walk(root)
    needle = query.lower()
    hits = []
    for f in files:
        if not fnmatch.fnmatch(f.name.lower(), file_pattern.lower()):
            continue
        try:
            if f.stat().st_size > 2_000_000:
                continue
            raw = f.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:1024]:
            continue
        for i, line in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
            if needle in line.lower():
                hits.append(f"{f}:{i}: {line.strip()[:200]}")
                if len(hits) >= 100:
                    return "\n".join(hits) + "\n… (abgeschnitten nach 100 Treffern)"
    return "\n".join(hits) or "Keine Treffer."


# ---------------------------------------------------------------- Internet


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "section", "article", "pre"}

    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.parts))
        return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def html_to_text(markup):
    parser = _TextExtractor()
    parser.feed(markup)
    return parser.text()


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "de,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        charset = r.headers.get_content_charset() or "utf-8"
        return r.headers.get_content_type(), r.read(3_000_000).decode(charset, "replace")


@tool(
    "fetch_url",
    "Lädt eine Webseite und gibt ihren lesbaren Text zurück.",
    {"url": {"type": "string", "description": "Die URL"}},
    ["url"],
)
def fetch_url(ctx, url):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    ctype, body = _get(url)
    text = html_to_text(body) if "html" in ctype else body
    return f"[{url}]\n{text}"


@tool(
    "web_search",
    "Sucht im Internet (DuckDuckGo) und liefert Titel, Links und Kurztexte.",
    {"query": {"type": "string", "description": "Suchanfrage"}},
    ["query"],
)
def web_search(ctx, query):
    _, body = _get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query))
    links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body, re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', body, re.S)
    results = []
    for i, (href, title) in enumerate(links[:8]):
        if "uddg=" in href:
            href = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
        snippet = html_to_text(snippets[i]) if i < len(snippets) else ""
        results.append(f"{i + 1}. {html.unescape(html_to_text(title))}\n   {href}\n   {snippet}")
    return "\n".join(results) or "Keine Ergebnisse (evtl. blockiert – versuche fetch_url mit einer konkreten Seite)."


# ---------------------------------------------------------------- Gedächtnis


@tool(
    "remember",
    "Speichert eine dauerhafte Notiz über den Nutzer oder seine Vorlieben im Gedächtnis "
    "(ist in allen künftigen Chats sichtbar).",
    {"fact": {"type": "string", "description": "Was gemerkt werden soll"}},
    ["fact"],
)
def remember(ctx, fact):
    ctx.home.mkdir(parents=True, exist_ok=True)
    with open(ctx.home / "memory.md", "a", encoding="utf-8") as f:
        f.write(f"- {fact.strip()}\n")
    return "Gemerkt."


def tool_list_text():
    """Werkzeugbeschreibung für Modelle ohne native Tool-Unterstützung."""
    lines = []
    for t in TOOLS.values():
        params = ", ".join(
            f"{k}{'' if k in t.required else '?'}: {v['type']}" for k, v in t.params.items()
        )
        lines.append(f"- {t.name}({params}): {t.description}")
    return "\n".join(lines)

