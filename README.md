# ⚡ Mirek die Macht alles

Ein **persönlicher KI-Agent, der komplett lokal auf deinem Computer läuft** – ähnlich wie OpenClaw, nur viel leichter. Mirek redet nicht nur, er **macht**: Befehle ausführen, Dateien & Dokumente bearbeiten, im Netz recherchieren, Bildschirm ansehen, Aufgaben für später planen und sogar sich selbst neue Fähigkeiten beibringen.

- 🧠 **Lokales KI-Modell** über [Ollama](https://ollama.com) **oder** jedes OpenAI-kompatible Programm (LM Studio, llama.cpp, Jan …). Keine Cloud, kein API-Key, keine Kosten, deine Daten bleiben bei dir.
- 🪶 **Federleicht**: reines Python, **null Pflicht-Abhängigkeiten**, kein `pip install`, kein Node, kein Docker.
- 🖥 **Drei Wege**: schöne Weboberfläche, Terminal, und optional **Telegram** (Mirek vom Handy aus, mit Fotos).
- 🛡 **Du behältst die Kontrolle**: gefährliche Aktionen fragen nach; besonders heikle Befehle immer – selbst im „Volle Macht"-Modus.
- ↩️ **Sicher**: Jede Dateiänderung wird gesichert, `undo_file` macht sie rückgängig, Gelöschtes landet im Papierkorb.

## 🚀 Start in 2 Minuten

**Voraussetzung:** [Python 3.9+](https://www.python.org/downloads/) (Windows: beim Installieren „Add to PATH" anhaken).

| System | So startest du |
|---|---|
| **Windows** | Doppelklick auf `start.bat` |
| **macOS / Linux** | `./start.sh` im Terminal |
| **Überall** | `python -m mirek` |

Beim ersten Start installiert Mirek bei Bedarf Ollama, startet es, lädt das KI-Modell (einmalig) und öffnet die Oberfläche unter <http://127.0.0.1:7860> – wenn möglich als eigenes App-Fenster.

## 🛠 Was Mirek alles kann (seine Werkzeuge)

**Dateien & Dokumente**
- Lesen, schreiben, bearbeiten, verschieben, kopieren, löschen (mit Sicherung & Papierkorb)
- `undo_file` – jede Änderung rückgängig machen
- Dateien & Text rekursiv suchen (nach Name, Größe, Alter, Inhalt)
- Liest **PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), OpenDocument, EPUB** direkt aus

**System & Programme**
- Beliebige Shell-Befehle (bash / PowerShell)
- Python-Code für Berechnungen, Daten, Automatisierung
- **Hintergrundprozesse** starten, Ausgabe ansehen, stoppen (Server, lange Aufgaben)
- Dateien, Ordner, Webseiten und **Programme** öffnen
- Systeminfos: CPU, RAM, Speicherplatz

**Internet**
- Websuche, Webseiten als lesbaren Text holen
- Dateien herunterladen
- **Web-APIs** aufrufen (JSON, Webhooks, Smart-Home …)

**Desktop**
- **Bildschirmfoto** machen und (mit Vision-Modell) ansehen
- Zwischenablage lesen & schreiben
- Desktop-Benachrichtigungen schicken

**Organisation & Automatik**
- **Gedächtnis**: Mirek merkt sich dauerhaft Dinge über dich
- **Arbeitsplan**: zeigt bei komplexen Aufgaben eine Checkliste
- **Geplante Aufgaben**: Erinnerungen und wiederkehrende Aufträge („jeden Morgen um 8 die Nachrichten zusammenfassen")
- **Eigene Plugins**: neue Werkzeuge als kleine Python-Dateien – Mirek kann sie sich selbst schreiben

**Bilder verstehen** 👁 – mit einem Vision-Modell (z.B. `qwen2.5vl`, `gemma3`) kannst du Fotos und Screenshots anhängen, und Mirek sieht sie wirklich.

## 🤖 Welches Modell?

Einstellbar in der Oberfläche unter ⚙︎. Mirek braucht für Werkzeuge ein **tool-fähiges** Modell.

| Modell | RAM | Wofür |
|---|---|---|
| `qwen3:4b` | 8 GB | schwächere Rechner |
| **`qwen3:8b`** (Standard) | 16 GB | guter Allrounder |
| `qwen3:14b` | 24 GB+ | deutlich schlauer |
| `gpt-oss:20b` | 16 GB+ (GPU) | sehr gut bei Werkzeugen |
| `qwen2.5vl`, `gemma3` | — | können **Bilder sehen** |

> 💡 Je größer das Modell, desto zuverlässiger erledigt Mirek mehrstufige Aufgaben. Mit sehr kleinen Modellen (1–2 B) klappen nur einfache Aufträge.

Modelle ohne native Werkzeug-Unterstützung funktionieren trotzdem – Mirek schaltet automatisch in einen Text-Modus.

### Lieber LM Studio & Co.?

In den Einstellungen die KI-Anbindung auf **„LM Studio / llama.cpp / Jan …"** stellen, Server-Adresse eintragen (LM Studio: `http://127.0.0.1:1234/v1`) und in deinem Programm den lokalen Server starten.

## 🛡 Erlaubnis-Modi & Sicherheit

| Modus | Bedeutung |
|---|---|
| **Nachfragen** (Standard) | Verändernde Aktionen (Befehl, Python, Schreiben, Öffnen, Download …) bestätigst du per Klick. Lesen & Suchen sofort. „Immer erlauben" merkt sich ein Werkzeug für die Sitzung. |
| **Volle Macht** | Mirek darf fast alles ohne Nachfrage. |
| **Nur lesen** | Mirek darf nichts verändern. |

**Immer** bestätigt werden müssen besonders gefährliche Befehle (Systemordner rekursiv löschen, Festplatte formatieren, Herunterfahren …) – auch bei „Volle Macht".

Die Oberfläche ist nur von deinem eigenen Rechner aus erreichbar (127.0.0.1) und durch einen geheimen Token geschützt, damit keine fremde Webseite Befehle auf deinem PC auslösen kann. Die Konfigurationsdatei (mit evtl. Tokens) wird mit eingeschränkten Rechten gespeichert.

## 💡 Beispiele

- „Sortiere meinen Downloads-Ordner nach Dateityp in Unterordner"
- „Fasse die PDF auf meinem Schreibtisch in 5 Stichpunkten zusammen"
- „Finde alle Fotos über 5 MB, die älter als ein Jahr sind"
- „Erinnere mich jeden Werktag um 9 Uhr, meine Mails zu checken"
- „Mach ein Bildschirmfoto und sag mir, was dort offen ist" (mit Vision-Modell)
- „Erstelle eine kleine Webseite mit einer To-do-Liste und öffne sie"
- „Merk dir: Ich heiße Mirek und mag kurze Antworten"

## ⌨️ Terminal-Modus

```bash
python -m mirek chat
```
Befehle: `/neu`, `/modus ask|auto|readonly`, `/ende`.

## 📱 Per Telegram steuern (optional)

1. In Telegram **@BotFather** → `/newbot` → Token kopieren.
2. Token in `~/.mirek/config.json` bei `"telegram_token"` eintragen.
3. `python -m mirek telegram` starten, dem Bot schreiben – er nennt dir deine Telegram-ID.
4. ID bei `"telegram_allowed_users": [12345678]` eintragen, neu starten.

Du kannst dem Bot auch **Fotos** schicken (mit Vision-Modell). Geplante Aufgaben melden sich automatisch bei dir.

## 🧩 Dateien, Gedächtnis, Skills & Plugins

Alles liegt in `~/.mirek/`:

| Ort | Inhalt |
|---|---|
| `config.json` | Einstellungen |
| `memory.md` | Was Mirek sich über dich gemerkt hat – frei bearbeitbar |
| `chats/` | Gespeicherte Chats |
| `tasks.json` | Geplante Aufgaben |
| `backups/` | Sicherungen & Papierkorb für `undo_file` |
| `skills/*.md` | Anleitungen für wiederkehrende Abläufe (erste Zeile = Beschreibung) |
| `plugins/*.py` | Eigene Werkzeuge |

**Eigenes Werkzeug (Plugin):** `~/.mirek/plugins/wetter.py`
```python
from mirek.tools import tool
from mirek.tools.web import fetch_url

@tool("wetter", "Wetter für eine Stadt.", {"stadt": {"type": "string"}}, ["stadt"])
def wetter(ctx, stadt):
    return fetch_url(ctx, f"https://wttr.in/{stadt}?format=3&lang=de")
```
Danach „lade deine Plugins neu" sagen – Mirek kann dir solche Werkzeuge auch selbst schreiben.

## ⚙️ Optionen

```
python -m mirek [web|chat|telegram|setup] [--model qwen3:14b] [--port 8080] [--mode ask|auto|readonly] [--no-browser]
```
Umgebungsvariablen: `MIREK_BACKEND` (ollama|openai), `MIREK_MODEL`, `MIREK_MODE`, `MIREK_PORT`, `MIREK_WORKSPACE`, `OLLAMA_HOST`, `MIREK_OPENAI_URL`, `MIREK_OPENAI_KEY`, `MIREK_HOME`.

**Optional** (macht Mirek besser, nicht nötig): `pip install pypdf` (bessere PDFs). Für Screenshots unter Linux: `gnome-screenshot`, `scrot` oder `grim`; Zwischenablage: `wl-clipboard` oder `xclip`.

## 🏗 Aufbau

```
mirek/
  agent.py       Denk-und-Handel-Schleife, Plan, Freigaben, Vision, Text-Modus
  llm.py         Anbindung an Ollama & OpenAI-kompatible Server
  server.py      Lokaler Web-Server
  scheduler.py   Geplante Aufgaben
  telegram.py    Telegram-Bot
  tools/         Alle Werkzeuge (files, system, web, docs, desktop, organize)
  web/           Oberfläche (eine HTML-Datei)
```

Tests: `python -m unittest discover -s tests` (30 Tests, keine Zusatzpakete nötig).
