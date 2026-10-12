"""Startpunkt: python -m mirek [web|chat|telegram|setup]"""

import argparse
import shutil
import subprocess
import sys
import time

from . import __version__, config
from .agent import Agent
from .llm import OllamaError, make_llm

DIM, BOLD, YEL, RED, GRN, RST = "\033[2m", "\033[1m", "\033[33m", "\033[31m", "\033[32m", "\033[0m"


def ensure_backend(cfg, pull=True):
    """Prüft die KI-Anbindung, startet Ollama bei Bedarf und lädt das Modell. True = bereit."""
    llm = make_llm(cfg)
    if cfg.get("backend") == "openai":
        try:
            llm.version()
            return True
        except OllamaError as e:
            print(f"{YEL}KI-Server ({cfg['openai_base_url']}) nicht erreichbar: {e}{RST}")
            print("Starte z.B. LM Studio und aktiviere dort den lokalen Server, dann erneut starten.")
            return False
    try:
        llm.version()
    except OllamaError:
        exe = shutil.which("ollama")
        if not exe:
            print(f"{YEL}Ollama ist nicht installiert.{RST} Bitte von https://ollama.com/download installieren.")
            if sys.platform.startswith("linux"):
                print("  Linux: curl -fsSL https://ollama.com/install.sh | sh")
            return False
        print("Starte Ollama …")
        kwargs = {"start_new_session": True} if sys.platform != "win32" else {"creationflags": 0x00000008}
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
        for _ in range(30):
            time.sleep(0.5)
            try:
                llm.version()
                break
            except OllamaError:
                continue
        else:
            print(f"{RED}Ollama ließ sich nicht starten.{RST}")
            return False
    if llm.has_model():
        return True
    if not pull:
        print(f"{YEL}Modell {cfg['model']} fehlt – du kannst es in der Oberfläche herunterladen.{RST}")
        return False
    print(f"Lade Modell {BOLD}{cfg['model']}{RST} herunter (einmalig, das kann dauern) …")
    try:
        for p in llm.pull():
            if p.get("total"):
                pct = 100 * p.get("completed", 0) / p["total"]
                print(f"\r  {p.get('status', '')[:30]:30} {pct:5.1f}%  ", end="", flush=True)
        print(f"\r{GRN}  Modell bereit.{' ' * 40}{RST}")
        return True
    except OllamaError as e:
        print(f"\n{RED}Download fehlgeschlagen: {e}{RST}")
        return False


def terminal_chat(cfg):
    if not ensure_backend(cfg):
        sys.exit(1)
    agent = Agent(cfg)
    print(f"{BOLD}⚡ Mirek die Macht alles{RST} – Modell {cfg['model']}, Modus {cfg['permission_mode']}")
    print(f"{DIM}Befehle: /neu, /modus ask|auto|readonly, /ende{RST}\n")

    def approve(req):
        try:
            answer = input(f"{YEL}  ⚠  {req['name']} ausführen? [j/N/i=immer] {RST}").strip().lower()
        except EOFError:
            return False
        if answer == "i":
            cfg["permission_mode"] = "auto"
        return answer in ("j", "ja", "y", "yes", "i")

    while True:
        try:
            text = input(f"{BOLD}{GRN}du ›{RST} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nTschüss! 👋")
            return
        if not text:
            continue
        if text in ("/ende", "/exit", "/quit"):
            return
        if text == "/neu":
            agent = Agent(cfg)
            print(f"{DIM}Neuer Chat.{RST}")
            continue
        if text.startswith("/modus"):
            mode = text.split()[-1]
            if mode in config.MODES:
                cfg["permission_mode"] = mode
            print(f"{DIM}Modus: {cfg['permission_mode']}{RST}")
            continue
        print(f"{BOLD}mirek ›{RST} ", end="", flush=True)
        try:
            for ev in agent.run(text, approve):
                kind = ev["type"]
                if kind == "token":
                    print(ev["text"], end="", flush=True)
                elif kind == "tool_call":
                    arg = next(iter(ev["args"].values()), "") if ev["args"] else ""
                    print(f"\n{DIM}  🔧 {ev['name']}: {str(arg).splitlines()[0][:100] if str(arg) else ''}{RST}")
                elif kind == "tool_result":
                    first = ev["output"].strip().splitlines()[:1]
                    mark = "✓" if ev["ok"] else "✗"
                    print(f"{DIM}  {mark} {first[0][:100] if first else ''}{RST}")
                    print(f"{BOLD}mirek ›{RST} ", end="", flush=True)
                elif kind in ("info", "error"):
                    print(f"\n{RED if kind == 'error' else DIM}{ev['text']}{RST}")
        except KeyboardInterrupt:
            agent.cancelled = True
            print(f"\n{DIM}(abgebrochen){RST}")
        print("\n")


def main():
    parser = argparse.ArgumentParser(prog="mirek", description="Mirek die Macht alles – lokaler KI-Agent")
    parser.add_argument("command", nargs="?", default="web", choices=["web", "chat", "telegram", "setup"],
                        help="web = Oberfläche (Standard), chat = Terminal, telegram = Bot, setup = nur vorbereiten")
    parser.add_argument("--model", help="Ollama-Modell, z.B. qwen3:8b")
    parser.add_argument("--port", type=int, help="Port der Weboberfläche")
    parser.add_argument("--mode", choices=config.MODES, help="Erlaubnis-Modus")
    parser.add_argument("--no-browser", action="store_true", help="Browser nicht automatisch öffnen")
    parser.add_argument("--version", action="version", version=f"Mirek {__version__}")
    args = parser.parse_args()

    cfg = config.load()
    if args.model:
        cfg["model"] = args.model
    if args.port:
        cfg["port"] = args.port
    if args.mode:
        cfg["permission_mode"] = args.mode

    if args.command == "setup":
        sys.exit(0 if ensure_backend(cfg) else 1)
    if args.command == "chat":
        terminal_chat(cfg)
    elif args.command == "telegram":
        from .scheduler import Scheduler
        from .telegram import run_bot

        if not ensure_backend(cfg):
            sys.exit(1)
        Scheduler(cfg).start()
        run_bot(cfg)
    else:
        from .server import serve

        ensure_backend(cfg)
        serve(cfg, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
