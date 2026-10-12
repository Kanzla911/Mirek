"""System: Befehle, Python, Hintergrundprozesse, Programme öffnen, Systeminfos."""

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from pathlib import Path

from . import as_bool, as_int, resolve, tool

WINDOWS = os.name == "nt"
# Windows-PowerShell soll UTF-8 ausgeben, sonst werden Umlaute zu Zeichensalat
PS_PREFIX = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $OutputEncoding=[System.Text.Encoding]::UTF8; "
DETACH = {"creationflags": 0x00000008 | 0x00000200} if WINDOWS else {"start_new_session": True}


def shell_args(command):
    if WINDOWS:
        return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", PS_PREFIX + command]
    shell = "/bin/bash" if Path("/bin/bash").exists() else "/bin/sh"
    return [shell, "-lc" if sys.platform == "darwin" else "-c", command]


def _env():
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["MIREK"] = "1"
    return env


@tool(
    "run_command",
    "Führt einen Shell-Befehl aus (Linux/macOS: bash, Windows: PowerShell) und liefert die Ausgabe. "
    "Für Programme, die weiterlaufen sollen (Server, Apps, lange Downloads), background=true setzen – "
    "die Ausgabe kannst du dann mit dem Werkzeug process ansehen.",
    {
        "command": {"type": "string", "description": "Der auszuführende Befehl"},
        "timeout": {"type": "integer", "description": "Zeitlimit in Sekunden (Standard 120)"},
        "background": {"type": "boolean", "description": "Im Hintergrund starten, nicht auf Ende warten"},
    },
    ["command"],
    dangerous=True,
)
def run_command(ctx, command, timeout=None, background=False):
    if as_bool(background):
        return start_process(ctx, command)
    limit = as_int(timeout, ctx.command_timeout)
    try:
        p = subprocess.run(
            shell_args(command), cwd=ctx.cwd, capture_output=True, timeout=limit,
            stdin=subprocess.DEVNULL, env=_env(),
        )
    except subprocess.TimeoutExpired as e:
        partial = (e.stdout or b"").decode("utf-8", "replace")
        return (f"Zeitüberschreitung nach {limit}s (Prozess beendet). Für lange Aufgaben background=true nutzen.\n"
                f"Bisherige Ausgabe:\n{partial}")
    out = f"Exit-Code: {p.returncode}\n"
    stdout, stderr = _decode(p.stdout), _decode(p.stderr)
    if stdout:
        out += stdout
    if stderr:
        out += f"\n[stderr]\n{stderr}"
    if not stdout and not stderr:
        out += "(keine Ausgabe)"
    return out


def _decode(data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252" if WINDOWS else "latin-1", "replace")


@tool(
    "run_python",
    "Führt Python-Code aus – ideal für Berechnungen, Daten, Excel/CSV/Word erzeugen, Bilder bearbeiten, "
    "Automatisierung. Ausgabe per print(). Fehlende Pakete installierst du mit run_command "
    "('<python> -m pip install paket').",
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
            [sys.executable, script], cwd=ctx.cwd, capture_output=True,
            timeout=as_int(timeout, ctx.command_timeout), stdin=subprocess.DEVNULL, env=_env(),
        )
    except subprocess.TimeoutExpired:
        return "Zeitüberschreitung."
    finally:
        os.unlink(script)
    stdout, stderr = _decode(p.stdout), _decode(p.stderr)
    out = stdout + (f"\n[stderr]\n{stderr}" if stderr else "")
    return f"Exit-Code: {p.returncode}\n{out or '(keine Ausgabe)'}"


# ---------------------------------------------------------------- Hintergrundprozesse

PROCS = {}


def start_process(ctx, command):
    log_dir = ctx.home / "procs"
    log_dir.mkdir(parents=True, exist_ok=True)
    pid_key = str(len(PROCS) + 1)
    log_path = log_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{pid_key}.log"
    log = open(log_path, "wb")
    p = subprocess.Popen(
        shell_args(command), cwd=ctx.cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        env=_env(), **DETACH,
    )
    PROCS[pid_key] = {"proc": p, "log": log_path, "command": command, "started": time.time(), "file": log}
    time.sleep(1.0)  # kurz warten, um Startfehler sofort zu sehen
    status = "läuft" if p.poll() is None else f"beendet (Exit-Code {p.returncode})"
    return f"Gestartet als Prozess #{pid_key} (PID {p.pid}), {status}.\nErste Ausgabe:\n{_tail(log_path, 20) or '(noch keine)'}"


def _tail(path, lines):
    try:
        data = Path(path).read_bytes()[-200_000:]
    except OSError:
        return ""
    return "\n".join(_decode(data).splitlines()[-lines:])


@tool(
    "process",
    "Verwaltet Hintergrundprozesse: action=start (command), list, output (id), stop (id).",
    {
        "action": {"type": "string", "enum": ["start", "list", "output", "stop"], "description": "Aktion"},
        "command": {"type": "string", "description": "Befehl für start"},
        "id": {"type": "string", "description": "Prozess-Nummer für output/stop"},
        "lines": {"type": "integer", "description": "Anzahl letzter Zeilen für output (Standard 60)"},
    },
    ["action"],
    dangerous=lambda a: a.get("action") in ("start", "stop"),
)
def process(ctx, action, command=None, id=None, lines=60):
    if action == "start":
        if not command:
            raise ValueError("command fehlt")
        return start_process(ctx, command)
    if action == "list":
        if not PROCS:
            return "Keine Hintergrundprozesse."
        rows = []
        for key, info in PROCS.items():
            code = info["proc"].poll()
            state = "läuft" if code is None else f"beendet ({code})"
            rows.append(f"#{key} [{state}] seit {time.strftime('%H:%M:%S', time.localtime(info['started']))}: {info['command'][:120]}")
        return "\n".join(rows)
    info = PROCS.get(str(id).lstrip("#"))
    if not info:
        raise KeyError(f"Prozess #{id} unbekannt. Nutze action=list.")
    if action == "output":
        code = info["proc"].poll()
        state = "läuft" if code is None else f"beendet (Exit-Code {code})"
        return f"Prozess #{id} {state}:\n{_tail(info['log'], as_int(lines, 60)) or '(keine Ausgabe)'}"
    if action == "stop":
        p = info["proc"]
        if p.poll() is None:
            if WINDOWS:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
            else:
                import signal

                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
        return f"Prozess #{id} gestoppt."
    raise ValueError(f"Unbekannte Aktion {action}")


# ---------------------------------------------------------------- Öffnen & Infos


@tool(
    "open",
    "Öffnet eine Datei, einen Ordner, eine Webseite (URL) oder ein Programm (z.B. 'firefox', 'Rechner', "
    "'notepad', 'Spotify') auf dem Computer des Nutzers.",
    {"target": {"type": "string", "description": "Pfad, URL oder Programmname"}},
    ["target"],
    dangerous=True,
)
def open_target(ctx, target):
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) and not re.match(r"^[a-z]:\\", target, re.I):
        webbrowser.open(target)
        return f"Geöffnet: {target}"
    path = resolve(ctx, target)
    system = platform.system()
    if path.exists():
        if system == "Windows":
            os.startfile(str(path))  # noqa: S606
        else:
            subprocess.Popen(["open" if system == "Darwin" else "xdg-open", str(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **DETACH)
        return f"Geöffnet: {path}"
    # Kein Pfad → als Programm behandeln
    if system == "Windows":
        subprocess.Popen(["powershell", "-NoProfile", "-Command", f"Start-Process '{target.replace(chr(39), '')}'"], **DETACH)
    elif system == "Darwin":
        r = subprocess.run(["open", "-a", target], capture_output=True, text=True)
        if r.returncode:
            raise FileNotFoundError(r.stderr.strip() or f"Programm {target} nicht gefunden")
    else:
        exe = shutil.which(target) or shutil.which(target.lower())
        if exe:
            subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **DETACH)
        elif shutil.which("gtk-launch"):
            subprocess.Popen(["gtk-launch", target.lower()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **DETACH)
        else:
            raise FileNotFoundError(f"Programm {target} nicht gefunden")
    return f"Programm gestartet: {target}"


def _memory():
    try:
        if sys.platform.startswith("linux"):
            info = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line)
            total = int(info["MemTotal"].split()[0]) * 1024
            free = int(info.get("MemAvailable", info["MemFree"]).split()[0]) * 1024
            return total, free
        if sys.platform == "darwin":
            total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout)
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
            pages = sum(int(x) for x in re.findall(r"Pages (?:free|inactive|speculative):\s+(\d+)", vm))
            return total, pages * 4096
        if WINDOWS:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong)] + [(f"x{i}", ctypes.c_ulonglong) for i in range(5)]

            ms = MS()
            ms.l = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return ms.total, ms.avail
    except Exception:
        pass
    return None, None


@tool("system_info", "Zeigt Betriebssystem, CPU, Arbeitsspeicher, Festplatten, Python und Uhrzeit.")
def system_info(ctx):
    from .files import human_size

    lines = [
        f"System: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Rechnername: {platform.node()}",
        f"CPU-Kerne: {os.cpu_count()}  ({platform.processor() or 'unbekannt'})",
    ]
    total, free = _memory()
    if total:
        lines.append(f"Arbeitsspeicher: {human_size(total)} gesamt, {human_size(free)} frei")
    roots = [f"{d}:\\" for d in "CDEFGH" if Path(f"{d}:\\").exists()] if WINDOWS else ["/", str(Path.home())]
    for r in dict.fromkeys(roots):
        try:
            du = shutil.disk_usage(r)
            lines.append(f"Speicher {r}: {human_size(du.free)} frei von {human_size(du.total)}")
        except OSError:
            pass
    lines.append(f"Python: {sys.executable} ({platform.python_version()})")
    lines.append(f"Jetzt: {time.strftime('%A, %d.%m.%Y %H:%M:%S')}")
    return "\n".join(lines)
