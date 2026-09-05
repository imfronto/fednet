#!/bin/sh
set -eu

# Wraps the stock `ollama serve` so a default model can be pulled
# automatically on first start, without a separate one-shot container
# showing up as "Exited" in `make ps`.

/bin/ollama serve &
SERVE_PID=$!

if [ -n "${OLLAMA_PULL_MODEL:-}" ]; then
  echo "ollama: waiting for server before pulling ${OLLAMA_PULL_MODEL}..."
  # Unbounded before this: a server that starts but never becomes
  # responsive hung here forever, invisible to the healthcheck (which
  # only checks the process exists, not that startup finished — see
  # services/ollama.yml). Bounded to 60s, then exit non-zero so
  # `restart: unless-stopped` actually cycles the container instead of
  # leaving it stuck.
  i=0
  until ollama list >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
      echo "ollama: server did not become ready after ${i}s, exiting so the container restarts" >&2
      exit 1
    fi
    sleep 1
  done
  ollama pull "$OLLAMA_PULL_MODEL" || echo "ollama: failed to pull ${OLLAMA_PULL_MODEL}, continuing anyway"
fi

wait "$SERVE_PID"
