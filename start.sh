#!/usr/bin/env bash
# Mirek starten (Linux / macOS). Doppelklick oder: ./start.sh  [chat|telegram]
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 fehlt. Bitte installieren: https://www.python.org/downloads/"
  exit 1
fi

# Ollama ist die einfachste KI-Anbindung. Wer lieber LM Studio o.ä. nutzt, kann das
# in den Einstellungen umstellen und MIREK_BACKEND=openai setzen, um diese Prüfung zu überspringen.
if [ "${MIREK_BACKEND}" != "openai" ] && ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama (lokale KI) ist noch nicht installiert."
  if [ "$(uname)" = "Darwin" ]; then
    echo "Bitte von https://ollama.com/download installieren und dann erneut starten."
    open "https://ollama.com/download" 2>/dev/null
    exit 1
  fi
  read -r -p "Jetzt automatisch installieren? [J/n] " answer
  if [ "${answer:-j}" != "n" ]; then
    curl -fsSL https://ollama.com/install.sh | sh || exit 1
  else
    echo "Du kannst Mirek auch mit LM Studio o.ä. nutzen – dann in den Einstellungen die KI-Anbindung umstellen."
    exit 1
  fi
fi

exec python3 -m mirek "$@"
