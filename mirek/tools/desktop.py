"""Desktop: Bildschirmfotos, Zwischenablage, Benachrichtigungen."""

import os
import shutil
import struct
import subprocess
import sys
import time

from . import NOTIFIERS, Result, tool

WINDOWS = os.name == "nt"
MAC = sys.platform == "darwin"


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", head[16:24])
    return None


def take_screenshot(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
            "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
            "$g=[System.Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size);"
            "$bmp.Save($env:MIREK_SHOT,[System.Drawing.Imaging.ImageFormat]::Png)"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], env={**os.environ, "MIREK_SHOT": str(path)},
                       capture_output=True, timeout=30)
    elif MAC:
        subprocess.run(["screencapture", "-x", str(path)], capture_output=True, timeout=30)
    else:
        candidates = [
            ["grim", str(path)],
            ["gnome-screenshot", "-f", str(path)],
            ["spectacle", "-b", "-n", "-o", str(path)],
            ["scrot", "-o", str(path)],
            ["import", "-window", "root", str(path)],
            ["xfce4-screenshooter", "-f", "-s", str(path)],
        ]
        for cmd in candidates:
            if shutil.which(cmd[0]):
                subprocess.run(cmd, capture_output=True, timeout=30)
                if path.exists() and path.stat().st_size:
                    break
    if not (path.exists() and path.stat().st_size):
        try:
            import pyautogui

            pyautogui.screenshot(str(path))
        except Exception as e:
            raise RuntimeError(
                "Bildschirmfoto nicht möglich. Linux: installiere z.B. 'gnome-screenshot', 'scrot' oder 'grim'."
            ) from e
    return path


def _shot(ctx, note=""):
    path = take_screenshot(ctx.home / "screenshots" / f"shot-{time.strftime('%Y%m%d-%H%M%S')}.png")
    size = png_size(path)
    dims = f" – Bildschirm {size[0]}×{size[1]} Pixel" if size else ""
    hint = "" if ctx.vision else " (Dein Modell kann Bilder nicht sehen – nutze ein Vision-Modell, um sie auszuwerten.)"
    return Result(f"{note}Bildschirmfoto: {path}{dims}.{hint}", [path])


@tool(
    "screenshot",
    "Macht ein Bildschirmfoto vom ganzen Bildschirm, damit du siehst, was gerade angezeigt wird.",
)
def screenshot(ctx):
    return _shot(ctx)


# ---------------------------------------------------------------- Zwischenablage


def _clip_cmds():
    if WINDOWS:
        return (["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"])
    if MAC:
        return ["pbpaste"], ["pbcopy"]
    for get, put in ((["wl-paste", "-n"], ["wl-copy"]),
                     (["xclip", "-selection", "clipboard", "-o"], ["xclip", "-selection", "clipboard", "-i"]),
                     (["xsel", "-b", "-o"], ["xsel", "-b", "-i"])):
        if shutil.which(get[0]):
            return get, put
    raise RuntimeError("Keine Zwischenablage gefunden – installiere 'wl-clipboard' oder 'xclip'.")


@tool(
    "clipboard",
    "Liest die Zwischenablage (action=get) oder schreibt Text hinein (action=set).",
    {
        "action": {"type": "string", "enum": ["get", "set"], "description": "get oder set"},
        "text": {"type": "string", "description": "Text für set"},
    },
    ["action"],
)
def clipboard(ctx, action, text=""):
    get, put = _clip_cmds()
    if action == "set":
        subprocess.run(put, input=text.encode("utf-8"), check=True, timeout=10)
        return f"In die Zwischenablage kopiert ({len(text)} Zeichen)."
    out = subprocess.run(get, capture_output=True, timeout=10).stdout.decode("utf-8", "replace")
    return out or "(Zwischenablage ist leer)"


# ---------------------------------------------------------------- Benachrichtigungen


def desktop_notification(title, message):
    env = {**os.environ, "MIREK_T": title[:120], "MIREK_M": message[:400]}
    try:
        if WINDOWS:
            script = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                      "$n=New-Object System.Windows.Forms.NotifyIcon;$n.Icon=[System.Drawing.SystemIcons]::Information;"
                      "$n.Visible=$true;$n.ShowBalloonTip(10000,$env:MIREK_T,$env:MIREK_M,"
                      "[System.Windows.Forms.ToolTipIcon]::Info);Start-Sleep 10;$n.Dispose()")
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script], env=env)
        elif MAC:
            subprocess.Popen(["osascript", "-e", "on run argv", "-e",
                              "display notification (item 2 of argv) with title (item 1 of argv)",
                              "-e", "end run", title, message])
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-a", "Mirek", title, message])
    except OSError:
        pass


NOTIFIERS.append(desktop_notification)


@tool(
    "notify",
    "Schickt dem Nutzer eine Benachrichtigung (Desktop, Mirek-Oberfläche und – falls eingerichtet – Telegram). "
    "Ideal für Erinnerungen und wenn eine lange Aufgabe fertig ist.",
    {
        "title": {"type": "string", "description": "Titel"},
        "message": {"type": "string", "description": "Nachricht"},
    },
    ["message"],
)
def notify(ctx, message, title="Mirek"):
    from . import notify_all

    notify_all(title, message)
    ctx.emit({"type": "notify", "title": title, "text": message})
    return "Benachrichtigung gesendet."
