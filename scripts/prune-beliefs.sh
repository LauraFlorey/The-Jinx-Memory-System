#!/usr/bin/env bash
# prune-beliefs.sh - Preview and prune stale belief entries

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$ROOT_DIR/beliefs-tools.sh"

MAX_AGE_DAYS=30
PROJECT_NAME=""
AUTO_YES="false"
POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes)
      AUTO_YES="true"
      shift
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        POSITIONAL_ARGS+=("$1")
        shift
      done
      ;;
    *)
      POSITIONAL_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ ${#POSITIONAL_ARGS[@]} -ge 1 ]] && [[ "${POSITIONAL_ARGS[0]}" =~ ^[0-9]+$ ]]; then
  MAX_AGE_DAYS="${POSITIONAL_ARGS[0]}"
  POSITIONAL_ARGS=("${POSITIONAL_ARGS[@]:1}")
fi

if [[ ${#POSITIONAL_ARGS[@]} -ge 1 ]]; then
  PROJECT_NAME="${POSITIONAL_ARGS[0]}"
fi

if [[ -n "$PROJECT_NAME" ]]; then
  BELIEFS="$ROOT_DIR/state/$PROJECT_NAME/beliefs.md"
else
  BELIEFS="$ROOT_DIR/state/beliefs.md"
fi

count_beliefs() {
  if [[ ! -f "$BELIEFS" ]]; then
    echo "0"
    return 0
  fi

  awk '/^<!-- added:/ { count++ } END { print count + 0 }' "$BELIEFS"
}

preview_prunable_beliefs() {
  if [[ ! -f "$BELIEFS" ]]; then
    return 0
  fi

  awk -v max_age_days="$MAX_AGE_DAYS" '
    function trim(value) {
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      return value
    }

    function now_epoch(cmd, result) {
      cmd = "date -u +%Y-%m-%dT%H:%M:%SZ"
      cmd | getline result
      close(cmd)
      return to_epoch(result)
    }

    function to_epoch(value, cmd, result) {
      cmd = "date -j -u -f \"%Y-%m-%dT%H:%M:%SZ\" \"" value "\" \"+%s\" 2>/dev/null"
      cmd | getline result
      close(cmd)
      return result + 0
    }

    function flush_entry(   age_days, epoch, removable) {
      if (title == "") {
        return
      }

      epoch = to_epoch(added)
      if (epoch > 0 && current_now > 0) {
        age_days = int((current_now - epoch) / 86400)
        if (age_days < 0) {
          age_days = 0
        }
      } else {
        age_days = 0
      }

      removable = (pinned != "true" && hits < 3 && age_days > max_age_days)
      if (removable) {
        printf "- %s | age=%sd | hits=%s | pinned=%s\n", title, age_days, hits, pinned
      }
    }

    BEGIN {
      current_now = now_epoch()
      hits = 1
      pinned = "false"
    }

    /^<!-- added:/ {
      flush_entry()
      added = $0
      sub(/^<!-- added:[[:space:]]*/, "", added)
      sub(/[[:space:]]*-->$/, "", added)
      title = ""
      hits = 1
      pinned = "false"
      next
    }

    /^<!-- hits:/ {
      hits = $0
      sub(/^<!-- hits:[[:space:]]*/, "", hits)
      sub(/[[:space:]]*-->$/, "", hits)
      hits += 0
      next
    }

    /^<!-- pinned:/ {
      pinned = $0
      sub(/^<!-- pinned:[[:space:]]*/, "", pinned)
      sub(/[[:space:]]*-->$/, "", pinned)
      pinned = trim(pinned)
      next
    }

    /^### Belief:/ {
      title = $0
      sub(/^### Belief:[[:space:]]*/, "", title)
      title = trim(title)
      next
    }

    END {
      flush_entry()
    }
  ' "$BELIEFS"
}

printf '[prune] Beliefs file: %s\n' "$BELIEFS"
printf '[prune] Max age: %s days\n' "$MAX_AGE_DAYS"

if [[ ! -f "$BELIEFS" ]]; then
  printf '[prune] No beliefs file found. Nothing to prune.\n'
  exit 0
fi

BEFORE_COUNT=$(count_beliefs)
PREVIEW_OUTPUT=$(preview_prunable_beliefs || true)

if [[ -n "$PREVIEW_OUTPUT" ]]; then
  printf '%s\n' "Entries that would be removed:"
  printf '%s\n' "$PREVIEW_OUTPUT"
else
  printf '%s\n' "No entries would be removed."
fi

if [[ "$AUTO_YES" != "true" ]]; then
  printf '%s' "Proceed with pruning? [y/N] "
  read -r confirmation
  case "$confirmation" in
    y|Y|yes|YES)
      ;;
    *)
      printf '%s\n' "Aborted."
      exit 0
      ;;
  esac
fi

cp "$BELIEFS" "$BELIEFS.bak"
prune_beliefs "$MAX_AGE_DAYS"

AFTER_COUNT=$(count_beliefs)
REMOVED_COUNT=$(awk -v before="$BEFORE_COUNT" -v after="$AFTER_COUNT" 'BEGIN {
  removed = before - after
  if (removed < 0) {
    removed = 0
  }
  print removed
}')

printf '[prune] Backup created: %s.bak\n' "$BELIEFS"
printf '[prune] Removed: %s\n' "$REMOVED_COUNT"
printf '[prune] Retained: %s\n' "$AFTER_COUNT"
