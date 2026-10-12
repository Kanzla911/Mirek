# ⚡ Mirek die Macht alles

Ein **persönlicher KI-Agent, der komplett lokal auf deinem Computer läuft** – ähnlich wie OpenClaw, nur viel leichter:

- 🧠 **Lokales KI-Modell** über [Ollama](https://ollama.com). Keine Cloud, kein API-Key, keine Kosten, deine Daten bleiben bei dir.
- 🛠 **Mirek macht Dinge selbst**: Befehle ausführen, Dateien lesen/schreiben/bearbeiten, Dateien suchen, im Internet suchen, Webseiten lesen, Python ausführen, Programme/Dateien/Webseiten öffnen.
- 🪶 **Federleicht**: nur Python, **null Abhängigkeiten**, kein `pip install`, kein Node, kein Docker. ~1.000 Zeilen Code.
- 🖥 **Schöne Weboberfläche** im Browser, **Terminal-Modus** und optional **Telegram** (Mirek vom Handy aus steuern).
- 🛡 **Du behältst die Kontrolle**: Gefährliche Aktionen fragen vorher nach – oder du gibst ihm „volle Macht“.
- 💾 **Gedächtnis & Skills**: Mirek merkt sich Dinge über dich und kann eigene Anleitungen nutzen.

## 🚀 Start in 2 Minuten

**Voraussetzung:** [Python 3.9+](https://www.python.org/downloads/) (Windows: beim Installieren „Add to PATH“ anhaken).

| System | So startest du |
|---|---|
| **Windows** | Doppelklick auf `start.bat` |
| **macOS / Linux** | `./start.sh` im Terminal |
| **Überall** | `python -m mirek` |

Beim ersten Start passiert automatisch:
1. Fehlt Ollama, wird es installiert (bzw. du bekommst den Download-Link).
2. Ollama wird gestartet und das KI-Modell heruntergeladen (einmalig, ca. 5 GB).
3. Der Browser öffnet sich mit Mirek unter <http://127.0.0.1:7860>.

## 🤖 Welches Modell?

Mirek braucht ein Modell, das **Werkzeuge benutzen** kann. Einstellbar in der Oberfläche unter ⚙︎ Einstellungen.

| Modell | RAM | Wofür |
|---|---|---|
| `qwen3:4b` | 8 GB | schwächere Rechner |
| **`qwen3:8b`** (Standard) | 16 GB | guter Allrounder |
| `qwen3:14b` | 24 GB+ | deutlich schlauer |
| `gpt-oss:20b` | 16 GB+ (mit GPU) | sehr gut bei Werkzeugen |

Modelle ohne native Werkzeug-Unterstützung funktionieren trotzdem – Mirek schaltet dann automatisch in einen Text-Modus.

## 🛡 Erlaubnis-Modi

| Modus | Bedeutung |
|---|---|
| **Nachfragen** (Standard) | Befehle, Python, Schreiben, Öffnen musst du mit einem Klick erlauben. Lesen & Suchen geht sofort. |
| **Volle Macht** | Mirek darf alles ohne Nachfrage. Nur nutzen, wenn du weißt, was du tust. |
| **Nur lesen** | Mirek darf nichts verändern. |

Die Weboberfläche ist nur von deinem eigenen Rechner aus erreichbar (127.0.0.1) und durch einen geheimen Token geschützt, damit keine fremde Webseite Befehle auf deinem PC auslösen kann.

## 💡 Beispiele

- „Sortiere meinen Downloads-Ordner nach Dateityp in Unterordner“
- „Wie viel Speicherplatz ist noch frei und was sind die größten Ordner?“
- „Suche die neuesten Nachrichten zu KI und fasse sie zusammen“
- „Erstelle eine kleine Webseite mit einer To-do-Liste und öffne sie“
- „Benenne alle Fotos in ~/Bilder/Urlaub nach Datum um“
- „Merk dir: Ich heiße Mirek und mag kurze Antworten“

## ⌨️ Terminal-Modus

```bash
python -m mirek chat
```
Befehle: `/neu` (neuer Chat), `/modus ask|auto|readonly`, `/ende`.

## 📱 Per Telegram steuern (optional)

1. In Telegram **@BotFather** öffnen → `/newbot` → Token kopieren.
2. Token in `~/.mirek/config.json` bei `"telegram_token"` eintragen.
3. `python -m mirek telegram` starten und dem Bot schreiben – er antwortet mit deiner Telegram-ID.
4. Diese ID bei `"telegram_allowed_users": [12345678]` eintragen und neu starten.

Nur eingetragene Nutzer dürfen Mirek steuern. Freigaben bestätigst du mit „✅ ja“.

## 🧩 Gedächtnis & Skills

Alles liegt in `~/.mirek/`:

| Datei | Inhalt |
|---|---|
| `config.json` | Einstellungen (Modell, Modus, Port, Telegram …) |
| `memory.md` | Was Mirek sich über dich gemerkt hat – frei bearbeitbar |
| `chats/` | Gespeicherte Chats |
| `skills/*.md` | Eigene Anleitungen. Die erste Zeile ist die Beschreibung. Mirek liest sie, wenn eine Aufgabe passt. |

Beispiel `~/.mirek/skills/backup.md`:
```markdown
# Backup meiner Dokumente auf den USB-Stick erstellen
1. Prüfe, ob /media/usb eingehängt ist.
2. Kopiere ~/Dokumente mit rsync -a nach /media/usb/backup-DATUM.
3. Melde, wie viele Dateien kopiert wurden.
```

## ⚙️ Optionen

```
python -m mirek [web|chat|telegram|setup] [--model qwen3:14b] [--port 8080] [--mode ask|auto|readonly] [--no-browser]
```
Umgebungsvariablen: `MIREK_MODEL`, `MIREK_MODE`, `MIREK_PORT`, `MIREK_WORKSPACE`, `OLLAMA_HOST`, `MIREK_HOME`.

## 🏗 Aufbau

```
mirek/
  agent.py     Agenten-Schleife: denken → Werkzeug → Ergebnis → … → Antwort
  tools.py     Alle Werkzeuge (neue = eine Funktion mit @tool)
  llm.py       Mini-Client für Ollama
  server.py    Lokaler Web-Server
  web/         Oberfläche (eine HTML-Datei)
  telegram.py  Telegram-Bot
```

Eigenes Werkzeug hinzufügen:
```python
@tool("wetter", "Liefert das Wetter für eine Stadt.", {"stadt": {"type": "string", "description": "Stadt"}}, ["stadt"])
def wetter(ctx, stadt):
    return fetch_url(ctx, f"https://wttr.in/{stadt}?format=3")
```

Tests: `python -m unittest discover -s tests`
