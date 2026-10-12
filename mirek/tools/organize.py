"""Organisation: Gedächtnis, Arbeitsplan, Zeitplanung, eigene Werkzeuge (Plugins)."""

from . import as_bool, load_plugins, tool


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


@tool(
    "forget",
    "Löscht Einträge aus dem Gedächtnis, die den Text enthalten.",
    {"text": {"type": "string", "description": "Text, der im zu löschenden Eintrag vorkommt"}},
    ["text"],
)
def forget(ctx, text):
    path = ctx.home / "memory.md"
    if not path.exists():
        return "Das Gedächtnis ist leer."
    lines = path.read_text("utf-8").splitlines(keepends=True)
    keep = [line for line in lines if text.lower() not in line.lower()]
    path.write_text("".join(keep), "utf-8")
    return f"{len(lines) - len(keep)} Eintrag/Einträge vergessen."


@tool(
    "update_plan",
    "Zeigt dem Nutzer deinen Arbeitsplan als Checkliste. Nutze es bei Aufgaben mit mehreren Schritten: "
    "am Anfang den Plan anlegen, dann nach jedem Schritt den Status aktualisieren.",
    {
        "steps": {
            "type": "array",
            "description": "Alle Schritte",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "status": {"type": "string", "enum": ["offen", "läuft", "erledigt"]},
                },
            },
        }
    },
    ["steps"],
)
def update_plan(ctx, steps):
    clean = []
    for s in steps if isinstance(steps, list) else []:
        if isinstance(s, str):
            s = {"text": s}
        status = str(s.get("status", "offen")).lower()
        status = {"done": "erledigt", "in_progress": "läuft", "pending": "offen", "todo": "offen"}.get(status, status)
        clean.append({"text": str(s.get("text", "")), "status": status if status in ("offen", "läuft", "erledigt") else "offen"})
    ctx.emit({"type": "plan", "steps": clean})
    done = sum(s["status"] == "erledigt" for s in clean)
    return f"Plan aktualisiert ({done}/{len(clean)} erledigt)."


@tool(
    "schedule",
    "Plant Aufgaben für später: Erinnerungen ('erinnere mich um 15 Uhr …') oder wiederkehrende Aufträge "
    "('jeden Tag um 8 die Nachrichten zusammenfassen'). action=add|list|remove. Zur geplanten Zeit erledigt "
    "Mirek den prompt selbst und benachrichtigt den Nutzer. autonomous=true erlaubt dabei auch verändernde "
    "Aktionen (Befehle, Dateien schreiben), sonst nur lesen & benachrichtigen.",
    {
        "action": {"type": "string", "enum": ["add", "list", "remove"], "description": "Aktion"},
        "prompt": {"type": "string", "description": "Was zur Zeit erledigt/gesagt werden soll"},
        "at": {"type": "string", "description": "Zeitpunkt, z.B. '14:30', 'morgen 8:00', '2026-12-24 18:00'"},
        "in_minutes": {"type": "number", "description": "Oder: in N Minuten"},
        "every_minutes": {"type": "number", "description": "Wiederholen alle N Minuten (1440 = täglich)"},
        "autonomous": {"type": "boolean", "description": "Darf selbstständig verändernde Aktionen ausführen"},
        "id": {"type": "string", "description": "Aufgaben-ID für remove"},
    },
    ["action"],
    dangerous=lambda a: a.get("action") == "add" and as_bool(a.get("autonomous")),
)
def schedule(ctx, action, prompt=None, at=None, in_minutes=None, every_minutes=None, autonomous=False, id=None):
    from .. import scheduler

    if action == "add":
        if not prompt:
            raise ValueError("prompt fehlt")
        task = scheduler.add_task(prompt, at, in_minutes, every_minutes, as_bool(autonomous))
        ctx.emit({"type": "tasks_changed"})
        return "Geplant: " + scheduler.describe(task)
    if action == "remove":
        ok = scheduler.remove_task(str(id))
        ctx.emit({"type": "tasks_changed"})
        return "Entfernt." if ok else f"Aufgabe {id} nicht gefunden."
    tasks = scheduler.load_tasks()
    return "\n".join(scheduler.describe(t) for t in tasks) or "Keine geplanten Aufgaben."


@tool(
    "reload_plugins",
    "Lädt eigene Werkzeuge aus ~/.mirek/plugins/*.py neu. So kannst du dir selbst neue Fähigkeiten bauen: "
    "Datei mit 'from mirek.tools import tool' und einer @tool-dekorierten Funktion fn(ctx, ...) schreiben, "
    "dann dieses Werkzeug aufrufen.",
)
def reload_plugins(ctx):
    results = load_plugins(ctx.home)
    if not results:
        return f"Keine Plugins in {ctx.home / 'plugins'}."
    return "\n".join(f"{name}: {'OK' if err is None else 'FEHLER ' + err}" for name, err in results)
