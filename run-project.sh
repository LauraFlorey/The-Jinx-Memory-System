#!/usr/bin/env bash
# run-project.sh — Convenience wrapper for running loop.sh by project name

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOOP="$SCRIPT_DIR/loop.sh"
MANIFEST_DIR="$SCRIPT_DIR/manifests"
PROJECTS_DIR="$SCRIPT_DIR/projects"

log() { printf '[project] %s\n' "$1"; }
warn() { printf '[warn] %s\n' "$1" >&2; }
error() { printf '[error] %s\n' "$1" >&2; }

list_projects() {
  local manifest

  for manifest in "$MANIFEST_DIR"/*.md; do
    [ -f "$manifest" ] || continue
    basename "$manifest" .md
  done | sort
}

available_projects_inline() {
  list_projects | awk '
    NF {
      if (count > 0) {
        printf ", "
      }
      printf "%s", $0
      count++
    }
    END {
      if (count == 0) {
        printf "none"
      }
      printf "\n"
    }
  '
}

if [ "$#" -eq 0 ]; then
  printf '%s\n' "Available projects:"
  list_projects | awk '{printf "  %s\n", $0}'
  exit 0
fi

PROJECT_NAME="$1"
shift

MANIFEST_REL="manifests/${PROJECT_NAME}.md"
MANIFEST_PATH="$SCRIPT_DIR/$MANIFEST_REL"
PROJECT_DIR="$PROJECTS_DIR/$PROJECT_NAME"
PROJECT_STATE="$PROJECT_DIR/state.md"

if [ ! -f "$MANIFEST_PATH" ]; then
  error "No manifest found for project: ${PROJECT_NAME}. Available: $(available_projects_inline)"
  exit 1
fi

log "Running: $PROJECT_NAME"
log "Manifest: $MANIFEST_REL"

if [ -d "$PROJECT_DIR" ]; then
  if [ -f "$PROJECT_STATE" ]; then
    log "Last state update: $(stat -f "%Sm" -t "%Y-%m-%d %H:%M:%S" "$PROJECT_STATE")"
  fi
else
  warn "Project directory not found: projects/${PROJECT_NAME}/"
fi

exec bash "$LOOP" "$MANIFEST_REL" "$@"
