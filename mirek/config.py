"""Konfiguration: ~/.mirek/config.json, überschreibbar per Umgebungsvariablen."""

import json
import os
from pathlib import Path

HOME = Path(os.environ.get("MIREK_HOME", Path.home() / ".mirek")).expanduser()

DEFAULTS = {
    # KI-Anbindung: "ollama" oder "openai" (LM Studio, llama.cpp, Jan, vLLM … – alles OpenAI-kompatible)
    "backend": "ollama",
    "model": "qwen3:8b",
    "ollama_host": "http://127.0.0.1:11434",
    "openai_base_url": "http://127.0.0.1:1234/v1",
    "openai_api_key": "",
    "num_ctx": 16384,
    "temperature": 0.3,
    "think": False,
    # Bilder ans Modell schicken: "auto" (fragt Ollama), true, false
    "vision": "auto",
    "workspace": str(Path.home()),
    # ask = gefährliche Aktionen bestätigen, auto = alles erlauben, readonly = nur lesen
    "permission_mode": "ask",
    "max_steps": 40,
    "command_timeout": 120,
    "port": 7860,
    "app_window": True,
    "telegram_token": "",
    "telegram_allowed_users": [],
}

ENV = {
    "MIREK_BACKEND": "backend",
    "MIREK_MODEL": "model",
    "OLLAMA_HOST": "ollama_host",
    "MIREK_OPENAI_URL": "openai_base_url",
    "MIREK_OPENAI_KEY": "openai_api_key",
    "MIREK_WORKSPACE": "workspace",
    "MIREK_MODE": "permission_mode",
    "MIREK_PORT": "port",
    "MIREK_TELEGRAM_TOKEN": "telegram_token",
}

MODES = ("ask", "auto", "readonly")
BACKENDS = ("ollama", "openai")


def normalize_host(host):
    host = str(host).strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    return host.replace("://0.0.0.0", "://127.0.0.1")


def coerce(key, value):
    """Wandelt Werte (z.B. aus Umgebungsvariablen oder der Oberfläche) in den richtigen Typ."""
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "ja", "yes", "on")
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    if isinstance(default, list):
        if isinstance(value, list):
            return value
        return [int(v) for v in str(value).replace(";", ",").split(",") if v.strip()]
    if key == "vision" and str(value).lower() in ("true", "false"):
        return str(value).lower() == "true"
    return value


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
            cfg[key] = coerce(key, os.environ[env])
    cfg["ollama_host"] = normalize_host(cfg["ollama_host"])
    if cfg["permission_mode"] not in MODES:
        cfg["permission_mode"] = "ask"
    if cfg["backend"] not in BACKENDS:
        cfg["backend"] = "ollama"
    return cfg


def save(cfg):
    HOME.mkdir(parents=True, exist_ok=True)
    stored = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    path = HOME / "config.json"
    path.write_text(json.dumps(stored, indent=2, ensure_ascii=False), "utf-8")
    try:
        path.chmod(0o600)  # enthält evtl. Tokens
    except OSError:
        pass
