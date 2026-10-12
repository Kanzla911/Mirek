"""Dateien: lesen, schreiben, bearbeiten, suchen, verschieben – mit Sicherungskopien und Rückgängig."""

import fnmatch
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from . import Result, as_bool, as_int, resolve, tool

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache", "AppData", "Library", ".Trash"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


# ---------------------------------------------------------------- Sicherungen


def _backup_dir(ctx):
    d = ctx.home / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def backup(ctx, path, action):
    """Sichert den aktuellen Stand von path, bevor er verändert wird. Nicht existierend = 'neu angelegt'."""
    path = Path(path)
    entry = {"orig": str(path), "backup": None, "time": time.time(), "action": action}
    if path.exists():
        dest = _backup_dir(ctx) / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}-{path.name}"
        if path.is_dir():
            if action != "delete":
                return  # Ordner werden nur beim Löschen gesichert (verschoben)
            shutil.move(str(path), dest)
        elif action == "delete":
            shutil.move(str(path), dest)
        else:
            shutil.copy2(path, dest)
        entry["backup"] = str(dest)
    with open(_backup_dir(ctx) / "index.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _entries(ctx):
    index = _backup_dir(ctx) / "index.jsonl"
    if not index.exists():
        return []
    return [json.loads(line) for line in index.read_text("utf-8").splitlines() if line.strip()]


@tool(
    "undo_file",
    "Macht die letzte Änderung (schreiben, bearbeiten, löschen, verschieben) an einer Datei rückgängig. "
    "Ohne path: zeigt die letzten Änderungen.",
    {"path": {"type": "string", "description": "Datei/Ordner, deren letzte Änderung rückgängig gemacht wird"}},
    dangerous=lambda a: bool(a.get("path")),
)
def undo_file(ctx, path=None):
    entries = _entries(ctx)
    if not path:
        if not entries:
            return "Noch keine Änderungen gesichert."
        return "\n".join(
            f"{time.strftime('%d.%m. %H:%M', time.localtime(e['time']))}  {e['action']:8} {e['orig']}" for e in entries[-25:]
        )
    target = str(resolve(ctx, path))
    for i in range(len(entries) - 1, -1, -1):
        e = entries[i]
        if e["orig"] != target or e.get("undone"):
            continue
        orig = Path(e["orig"])
        if e["backup"] is None:  # war neu angelegt → entfernen
            if orig.is_dir():
                shutil.rmtree(orig)
            elif orig.exists():
                orig.unlink()
            msg = f"{orig} entfernt (war neu angelegt)."
        else:
            orig.parent.mkdir(parents=True, exist_ok=True)
            if orig.is_file():
                orig.unlink()
            if e["action"] == "delete" or Path(e["backup"]).is_dir():
                shutil.move(e["backup"], orig)
            else:
                shutil.copy2(e["backup"], orig)
            msg = f"{orig} wiederhergestellt (Stand vor '{e['action']}')."
        entries[i]["undone"] = True
        with open(_backup_dir(ctx) / "index.jsonl", "w", encoding="utf-8") as f:
            f.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in entries)
        return msg
    raise FileNotFoundError(f"Keine Sicherung für {target} gefunden.")


# ---------------------------------------------------------------- Lesen


@tool(
    "read_file",
    "Liest eine Datei. Versteht Text, Code, PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), "
    "OpenDocument und Bilder (Bilder werden dir gezeigt, wenn dein Modell sehen kann).",
    {
        "path": {"type": "string", "description": "Dateipfad"},
        "start_line": {"type": "integer", "description": "Erste Zeile (ab 1)"},
        "max_lines": {"type": "integer", "description": "Maximale Anzahl Zeilen (Standard 400)"},
    },
    ["path"],
)
def read_file(ctx, path, start_line=1, max_lines=400):
    from .docs import DOC_EXT, extract_text

    p = resolve(ctx, path)
    if p.is_dir():
        raise IsADirectoryError(f"{p} ist ein Ordner – nutze list_dir.")
    suffix = p.suffix.lower()
    if suffix in IMAGE_EXT:
        note = "" if ctx.vision else " (Dein aktuelles Modell kann keine Bilder sehen – nutze ein Vision-Modell wie qwen2.5vl oder gemma3.)"
        return Result(f"Bild {p} ({p.stat().st_size} Bytes).{note}", [p])
    if suffix in DOC_EXT:
        text = extract_text(p)
    else:
        raw = p.read_bytes()
        if b"\0" in raw[:2048]:
            return f"{p} ist eine Binärdatei ({len(raw)} Bytes)."
        text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    start = max(as_int(start_line, 1), 1)
    end = min(start - 1 + as_int(max_lines, 400), len(lines))
    more = f" – weiter mit start_line={end + 1}" if end < len(lines) else ""
    return f"[{p} – Zeilen {start}-{end} von {len(lines)}{more}]\n" + "\n".join(lines[start - 1:end])


@tool(
    "list_dir",
    "Listet den Inhalt eines Ordners auf (mit Größe und Änderungsdatum).",
    {
        "path": {"type": "string", "description": "Ordner (Standard: aktuelles Verzeichnis)"},
        "show_hidden": {"type": "boolean", "description": "Versteckte Dateien zeigen"},
    },
)
def list_dir(ctx, path=".", show_hidden=False):
    p = resolve(ctx, path)
    entries = [e for e in p.iterdir() if as_bool(show_hidden) or not e.name.startswith(".")]
    entries.sort(key=lambda e: (not e.is_dir(), e.name.lower()))
    lines = [f"[{p}] {len(entries)} Einträge"]
    for e in entries[:300]:
        try:
            st = e.stat()
            date = time.strftime("%d.%m.%Y %H:%M", time.localtime(st.st_mtime))
            lines.append(f"{e.name}/  ({date})" if e.is_dir() else f"{e.name}  ({human_size(st.st_size)}, {date})")
        except OSError:
            lines.append(e.name)
    if len(entries) > 300:
        lines.append(f"… und {len(entries) - 300} weitere")
    return "\n".join(lines)


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def walk(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            yield Path(dirpath) / name


@tool(
    "find_files",
    "Sucht Dateien rekursiv nach Namensmuster (z.B. '*.pdf', '*rechnung*'). Optional nach Größe/Alter filtern.",
    {
        "pattern": {"type": "string", "description": "Glob-Muster"},
        "path": {"type": "string", "description": "Startordner"},
        "min_size_mb": {"type": "number", "description": "Nur Dateien ab dieser Größe"},
        "modified_within_days": {"type": "integer", "description": "Nur in den letzten N Tagen geändert"},
    },
    ["pattern"],
)
def find_files(ctx, pattern, path=".", min_size_mb=None, modified_within_days=None):
    root = resolve(ctx, path)
    pattern = pattern.lower()
    min_bytes = float(min_size_mb) * 1024 * 1024 if min_size_mb else 0
    since = time.time() - int(modified_within_days) * 86400 if modified_within_days else 0
    hits = []
    for f in walk(root):
        if not fnmatch.fnmatch(f.name.lower(), pattern):
            continue
        if min_bytes or since:
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_size < min_bytes or st.st_mtime < since:
                continue
            hits.append(f"{f}  ({human_size(st.st_size)})")
        else:
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
    files = [root] if root.is_file() else walk(root)
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


# ---------------------------------------------------------------- Schreiben


@tool(
    "write_file",
    "Schreibt Text in eine Datei (legt Ordner automatisch an). Überschreibt, außer append=true. "
    "Vorherige Version wird gesichert (undo_file).",
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
    backup(ctx, p, "append" if as_bool(append) else "write")
    with open(p, "a" if as_bool(append) else "w", encoding="utf-8") as f:
        f.write(content)
    return f"Gespeichert: {p} ({len(content)} Zeichen)"


@tool(
    "edit_file",
    "Ersetzt einen exakten Textabschnitt in einer Datei. old_text muss genau einmal vorkommen "
    "(oder replace_all=true). Vorherige Version wird gesichert.",
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
        raise ValueError("old_text wurde nicht gefunden – lies die Datei zuerst mit read_file und kopiere den Text exakt.")
    if count > 1 and not as_bool(replace_all):
        raise ValueError(f"old_text kommt {count}× vor – mehr Kontext angeben oder replace_all=true.")
    backup(ctx, p, "edit")
    p.write_text(text.replace(old_text, new_text), "utf-8")
    return f"{p}: {count if as_bool(replace_all) else 1} Stelle(n) ersetzt."


@tool(
    "move_path",
    "Verschiebt oder benennt eine Datei/einen Ordner um.",
    {
        "source": {"type": "string", "description": "Quelle"},
        "destination": {"type": "string", "description": "Ziel (Ordner oder neuer Name)"},
    },
    ["source", "destination"],
    dangerous=True,
)
def move_path(ctx, source, destination):
    src, dst = resolve(ctx, source), resolve(ctx, destination)
    if not src.exists():
        raise FileNotFoundError(src)
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        raise FileExistsError(f"{dst} existiert schon.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), dst)
    return f"Verschoben: {src} → {dst}"


@tool(
    "copy_path",
    "Kopiert eine Datei oder einen ganzen Ordner.",
    {
        "source": {"type": "string", "description": "Quelle"},
        "destination": {"type": "string", "description": "Ziel"},
    },
    ["source", "destination"],
    dangerous=True,
)
def copy_path(ctx, source, destination):
    src, dst = resolve(ctx, source), resolve(ctx, destination)
    if dst.is_dir():
        dst = dst / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        backup(ctx, dst, "write")
        shutil.copy2(src, dst)
    return f"Kopiert: {src} → {dst}"


@tool(
    "delete_path",
    "Löscht eine Datei oder einen Ordner. Sicher: wird in den Mirek-Papierkorb verschoben "
    "und kann mit undo_file wiederhergestellt werden.",
    {"path": {"type": "string", "description": "Was gelöscht werden soll"}},
    ["path"],
    dangerous=True,
)
def delete_path(ctx, path):
    p = resolve(ctx, path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p == Path(p.anchor) or p == Path.home():
        raise PermissionError("Das Wurzelverzeichnis oder den Benutzerordner lösche ich nicht.")
    backup(ctx, p, "delete")
    return f"Gelöscht (wiederherstellbar mit undo_file): {p}"


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
