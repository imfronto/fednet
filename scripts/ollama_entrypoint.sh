#!/bin/sh
set -eu

# Wraps the stock `ollama serve` so a default model can be pulled
# automatically on first start, without a separate one-shot container
# showing up as "Exited" in `make ps`.

/bin/ollama serve &
SERVE_PID=$!

if [ -n "${OLLAMA_PULL_MODEL:-}" ]; then
  echo "ollama: waiting for server before pulling ${OLLAMA_PULL_MODEL}..."
  until ollama list >/dev/null 2>&1; do
    sleep 1
  done
  ollama pull "$OLLAMA_PULL_MODEL" || echo "ollama: failed to pull ${OLLAMA_PULL_MODEL}, continuing anyway"
fi

wait "$SERVE_PID"
