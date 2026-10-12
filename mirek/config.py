"""Konfiguration: ~/.mirek/config.json, überschreibbar per Umgebungsvariablen."""

import json
import os
from pathlib import Path

HOME = Path(os.environ.get("MIREK_HOME", Path.home() / ".mirek")).expanduser()

DEFAULTS = {
    "model": "qwen3:8b",
    "ollama_host": "http://127.0.0.1:11434",
    "num_ctx": 16384,
    "temperature": 0.3,
    "think": False,
    "workspace": str(Path.home()),
    # ask = gefährliche Aktionen bestätigen, auto = alles erlauben, readonly = nur lesen
    "permission_mode": "ask",
    "max_steps": 30,
    "command_timeout": 120,
    "port": 7860,
    "telegram_token": "",
    "telegram_allowed_users": [],
}

ENV = {
    "MIREK_MODEL": "model",
    "OLLAMA_HOST": "ollama_host",
    "MIREK_WORKSPACE": "workspace",
    "MIREK_MODE": "permission_mode",
    "MIREK_PORT": "port",
    "MIREK_TELEGRAM_TOKEN": "telegram_token",
}

MODES = ("ask", "auto", "readonly")


def normalize_host(host):
    host = host.strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host.replace("://0.0.0.0", "://127.0.0.1")


def load():
    HOME.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    path = HOME / "config.json"
    if path.exists():
        cfg.update(json.loads(path.read_text("utf-8")))
    else:
        save(cfg)
    for env, key in ENV.items():
        if os.environ.get(env):
            value = os.environ[env]
            cfg[key] = int(value) if isinstance(DEFAULTS[key], int) and not isinstance(DEFAULTS[key], bool) else value
    cfg["ollama_host"] = normalize_host(cfg["ollama_host"])
    if cfg["permission_mode"] not in MODES:
        cfg["permission_mode"] = "ask"
    return cfg


def save(cfg):
    HOME.mkdir(parents=True, exist_ok=True)
    stored = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    (HOME / "config.json").write_text(json.dumps(stored, indent=2, ensure_ascii=False), "utf-8")
