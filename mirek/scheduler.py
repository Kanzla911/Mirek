"""Geplante Aufgaben: Erinnerungen und wiederkehrende Aufträge („jeden Morgen um 8 …“)."""

import json
import re
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta

from . import config

_lock = threading.Lock()


def _path():
    return config.HOME / "tasks.json"


def load_tasks():
    try:
        return json.loads(_path().read_text("utf-8"))
    except (OSError, ValueError):
        return []


def save_tasks(tasks):
    config.HOME.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(tasks, ensure_ascii=False, indent=1), "utf-8")


def parse_time(text, now=None):
    """Versteht '14:30', 'morgen 8:00', '2026-10-13 09:15', ISO-Zeitstempel."""
    now = now or datetime.now()
    text = str(text).strip().lower()
    m = re.fullmatch(r"(?:(heute|morgen|übermorgen)\s*(?:um\s*)?)?(\d{1,2})[:.](\d{2})(?:\s*uhr)?", text)
    if m:
        day = {"heute": 0, None: 0, "morgen": 1, "übermorgen": 2}[m.group(1)]
        when = now.replace(hour=int(m.group(2)), minute=int(m.group(3)), second=0, microsecond=0) + timedelta(days=day)
        if m.group(1) is None and when <= now:
            when += timedelta(days=1)
        return when
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m. %H:%M"):
        try:
            when = datetime.strptime(text, fmt)
            if fmt == "%d.%m. %H:%M":
                when = when.replace(year=now.year)
            return when
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        raise ValueError(f"Zeit '{text}' nicht verstanden. Beispiele: '14:30', 'morgen 8:00', '2026-12-24 18:00'.") from None


def add_task(prompt, at=None, in_minutes=None, every_minutes=None, autonomous=False, now=None):
    now = now or datetime.now()
    if at:
        first = parse_time(at, now)
    elif in_minutes is not None:
        first = now + timedelta(minutes=float(in_minutes))
    elif every_minutes is not None:
        first = now + timedelta(minutes=float(every_minutes))
    else:
        raise ValueError("Gib at, in_minutes oder every_minutes an.")
    task = {
        "id": uuid.uuid4().hex[:8], "prompt": prompt, "next_run": first.timestamp(),
        "every_minutes": float(every_minutes) if every_minutes else None,
        "autonomous": bool(autonomous), "enabled": True, "created": now.timestamp(), "last_result": None,
    }
    with _lock:
        tasks = load_tasks()
        tasks.append(task)
        save_tasks(tasks)
    return task


def remove_task(task_id):
    with _lock:
        tasks = load_tasks()
        left = [t for t in tasks if t["id"] != task_id]
        save_tasks(left)
    return len(left) != len(tasks)


def set_enabled(task_id, enabled):
    with _lock:
        tasks = load_tasks()
        for t in tasks:
            if t["id"] == task_id:
                t["enabled"] = bool(enabled)
        save_tasks(tasks)


def describe(task):
    when = datetime.fromtimestamp(task["next_run"]).strftime("%d.%m.%Y %H:%M")
    repeat = f", alle {task['every_minutes']:g} min" if task.get("every_minutes") else ""
    state = "" if task.get("enabled", True) else " [pausiert]"
    power = " ⚡selbstständig" if task.get("autonomous") else ""
    return f"[{task['id']}] {when}{repeat}{power}{state}: {task['prompt']}"


class Scheduler(threading.Thread):
    """Prüft regelmäßig, ob Aufgaben fällig sind, und lässt Mirek sie erledigen."""

    def __init__(self, cfg, interval=15):
        super().__init__(daemon=True, name="mirek-scheduler")
        self.cfg = cfg
        self.interval = interval
        self.stop_event = threading.Event()
        self.running = set()

    def run(self):
        while not self.stop_event.wait(self.interval):
            self.tick()

    def tick(self, now=None):
        now = now or time.time()
        due = []
        with _lock:
            tasks = load_tasks()
            for t in tasks:
                if t.get("enabled", True) and t["next_run"] <= now and t["id"] not in self.running:
                    due.append(dict(t))
                    if t.get("every_minutes"):
                        while t["next_run"] <= now:
                            t["next_run"] += t["every_minutes"] * 60
                    else:
                        t["enabled"] = False
            save_tasks(tasks)
        threads = []
        for t in due:
            self.running.add(t["id"])
            th = threading.Thread(target=self.execute, args=(t,), daemon=True)
            th.start()
            threads.append(th)
        return threads

    def execute(self, task):
        from .agent import Agent
        from .tools import notify_all

        try:
            cfg = dict(self.cfg)
            # Selbstständige Aufgaben dürfen handeln (vom Nutzer beim Anlegen erlaubt), andere nur lesen
            cfg["permission_mode"] = "auto" if task.get("autonomous") else "readonly"
            agent = Agent(cfg, session_id=f"task-{task['id']}")
            agent.title = agent.title or f"⏰ {task['prompt'][:50]}"
            prompt = (f"[Geplante Aufgabe, ausgelöst am {datetime.now():%d.%m.%Y %H:%M}] {task['prompt']}\n"
                      "Der Nutzer ist evtl. nicht am Rechner – antworte mit einer kurzen Zusammenfassung.")
            answer = ""
            for ev in agent.run(prompt, lambda req: False):
                if ev["type"] == "token":
                    answer += ev["text"]
                elif ev["type"] == "tool_call":
                    answer = ""
                elif ev["type"] == "error":
                    answer += f"\n⚠️ {ev['text']}"
            answer = answer.strip() or "Erledigt."
            notify_all(f"⏰ {task['prompt'][:60]}", answer)
            with _lock:
                tasks = load_tasks()
                for t in tasks:
                    if t["id"] == task["id"]:
                        t["last_result"] = answer[:2000]
                        t["last_run"] = time.time()
                save_tasks(tasks)
        except Exception:
            traceback.print_exc()
        finally:
            self.running.discard(task["id"])
