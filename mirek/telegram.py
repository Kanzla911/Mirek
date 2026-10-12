"""Optional: Mirek per Telegram vom Handy aus steuern."""

import json
import threading
import urllib.error
import urllib.request

from . import config
from .agent import Agent

APPROVE_WORDS = {"ja", "j", "yes", "y", "✅ ja", "ok"}
KEYBOARD = {"keyboard": [["✅ ja", "❌ nein"]], "one_time_keyboard": True, "resize_keyboard": True}


class Bot:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base = f"https://api.telegram.org/bot{cfg['telegram_token']}/"
        self.allowed = {int(u) for u in cfg.get("telegram_allowed_users") or []}
        self.agents, self.pending, self.running = {}, {}, set()

    def call(self, method, http_timeout=40, **params):
        req = urllib.request.Request(
            self.base + method, data=json.dumps(params).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=http_timeout) as r:
            return json.load(r).get("result")

    def send(self, chat_id, text, keyboard=None):
        for i in range(0, max(len(text), 1), 4000):
            params = {"chat_id": chat_id, "text": text[i:i + 4000] or "…"}
            if keyboard:
                params["reply_markup"] = keyboard
            try:
                self.call("sendMessage", **params)
            except urllib.error.URLError as e:
                print("Telegram-Fehler:", e)

    def handle(self, chat_id, user_id, text):
        if user_id not in self.allowed:
            self.send(chat_id, f"⛔ Kein Zugriff. Deine Telegram-ID ist {user_id}.\n"
                               f"Trage sie in {config.HOME / 'config.json'} unter telegram_allowed_users ein.")
            return
        if chat_id in self.pending:
            entry = self.pending[chat_id]
            entry["allow"] = text.strip().lower() in APPROVE_WORDS
            entry["event"].set()
            return
        if text.strip() == "/neu" or text.strip() == "/start":
            self.agents.pop(chat_id, None)
            self.send(chat_id, "⚡ Neuer Chat. Was soll ich erledigen?")
            return
        if text.strip() == "/stop" and chat_id in self.agents:
            self.agents[chat_id].cancelled = True
            return
        if chat_id in self.running:
            self.send(chat_id, "⏳ Ich arbeite noch… (/stop zum Abbrechen)")
            return
        self.running.add(chat_id)
        threading.Thread(target=self.work, args=(chat_id, text), daemon=True).start()

    def work(self, chat_id, text):
        agent = self.agents.get(chat_id)
        if agent is None:
            agent = self.agents[chat_id] = Agent(self.cfg, session_id=f"telegram-{chat_id}")

        def approve(req):
            event = threading.Event()
            self.pending[chat_id] = {"event": event, "allow": False}
            args = json.dumps(req["args"], ensure_ascii=False)[:600]
            self.send(chat_id, f"⚠️ Darf ich {req['name']} ausführen?\n{args}", KEYBOARD)
            event.wait(600)
            return self.pending.pop(chat_id)["allow"]

        answer = ""
        try:
            self.call("sendChatAction", chat_id=chat_id, action="typing")
            for ev in agent.run(text, approve):
                if ev["type"] == "token":
                    answer += ev["text"]
                elif ev["type"] == "tool_call":
                    if answer.strip():
                        self.send(chat_id, answer.strip())
                    answer = ""
                    arg = next(iter(ev["args"].values()), "") if ev["args"] else ""
                    self.send(chat_id, f"🔧 {ev['name']}: {str(arg)[:200]}")
                elif ev["type"] in ("info", "error"):
                    answer += f"\n{ev['text']}"
            self.send(chat_id, answer.strip() or "✅ Erledigt.")
        except Exception as e:  # der Bot soll weiterlaufen
            self.send(chat_id, f"Fehler: {e}")
        finally:
            self.running.discard(chat_id)

    def loop(self):
        me = self.call("getMe")
        print(f"🤖 Telegram-Bot @{me['username']} läuft. (Strg+C beendet)")
        if not self.allowed:
            print("⚠️  Noch keine erlaubten Nutzer. Schreib dem Bot – er antwortet mit deiner ID.")
        offset = 0
        while True:
            try:
                updates = self.call("getUpdates", http_timeout=70, offset=offset, timeout=50) or []
            except (urllib.error.URLError, TimeoutError, OSError):
                continue
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                if msg.get("text"):
                    self.handle(msg["chat"]["id"], msg["from"]["id"], msg["text"])


def run_bot(cfg):
    if not cfg.get("telegram_token"):
        print("Kein Telegram-Token. So geht's:\n"
              "  1. In Telegram @BotFather öffnen → /newbot → Token kopieren\n"
              f"  2. In {config.HOME / 'config.json'} bei \"telegram_token\" eintragen\n"
              "  3. python -m mirek telegram erneut starten")
        return
    try:
        Bot(cfg).loop()
    except KeyboardInterrupt:
        print("\nTschüss! 👋")
