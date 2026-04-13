#!/usr/bin/env bash
# start-discord.sh — Convenience launcher for the Discord bot

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_SCRIPT="$SCRIPT_DIR/discord-bot.py"
ENV_FILE="$SCRIPT_DIR/.env"
LAUNCH_LOG="$SCRIPT_DIR/logs/discord-bot-launch.log"
MAX_RESTARTS=3
RESTART_COUNT=0
AUTO_RESTART="${DISCORD_BOT_AUTO_RESTART:-}"

mkdir -p "$SCRIPT_DIR/logs"

log() {
  printf '[start-discord] %s\n' "$1"
}

warn() {
  printf '[start-discord][warn] %s\n' "$1" >&2
}

error() {
  printf '[start-discord][error] %s\n' "$1" >&2
}

load_env_file() {
  if [[ -f "$ENV_FILE" ]]; then
    log "Loading environment from .env"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
}

verify_required_env() {
  local missing=()
  local name

  for name in AGENT_LOOP_API_KEY DISCORD_BOT_TOKEN DISCORD_CHANNEL_ID; do
    if [[ -z "${!name:-}" ]]; then
      missing+=("$name")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    error "Missing required environment variables:"
    printf '  - %s\n' "${missing[@]}" >&2
    printf '\n' >&2
    printf '%s\n' "Add them to $ENV_FILE or export them in your shell, then run ./start-discord.sh again." >&2
    return 1
  fi
}

ensure_dependencies() {
  if python3 -c "import discord" 2>/dev/null; then
    return 0
  fi

  warn "discord.py is not installed for python3."
  log "Install command: pip3 install discord.py aiohttp pyyaml --break-system-packages"

  if [[ ! -t 0 ]]; then
    error "Cannot prompt for installation in a non-interactive shell."
    return 1
  fi

  local answer
  read -r -p "Install required packages now? [y/N] " answer
  if [[ "$answer" =~ ^[Yy]$ ]]; then
    pip3 install discord.py aiohttp pyyaml --break-system-packages
  else
    error "Dependencies not installed."
    return 1
  fi
}

should_restart() {
  local exit_code="$1"
  local answer

  if (( exit_code == 130 || exit_code == 143 )); then
    return 1
  fi

  if [[ "$AUTO_RESTART" == "1" || "$AUTO_RESTART" == "true" || "$AUTO_RESTART" == "yes" ]]; then
    return 0
  fi

  if [[ ! -t 0 ]]; then
    return 1
  fi

  read -r -p "Restart the Discord bot? [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]]
}

start_bot() {
  local exit_code

  while true; do
    log "Starting Discord bot"
    if {
      printf '\n[%s] Starting discord-bot.py\n' "$(date)"
      python3 "$BOT_SCRIPT"
    } >> "$LAUNCH_LOG" 2>&1; then
      log "Discord bot exited normally"
      return 0
    else
      exit_code=$?
    fi

    {
      printf '[%s] discord-bot.py exited with code %s\n' "$(date)" "$exit_code"
    } >> "$LAUNCH_LOG"
    warn "Discord bot exited with code $exit_code. See $LAUNCH_LOG"

    if (( RESTART_COUNT >= MAX_RESTARTS )); then
      error "Max restart count reached ($MAX_RESTARTS)."
      return "$exit_code"
    fi

    if ! should_restart "$exit_code"; then
      return "$exit_code"
    fi

    RESTART_COUNT=$((RESTART_COUNT + 1))
    log "Restarting bot ($RESTART_COUNT/$MAX_RESTARTS)"
  done
}

main() {
  load_env_file
  verify_required_env
  ensure_dependencies
  start_bot
}

main "$@"
