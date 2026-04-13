#!/usr/bin/env bash
# run-council.sh — Manual orchestrator for research council runs
# Runs multiple loop.sh invocations with different manifests
# Each subagent gets only the context it needs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOOP="$ROOT_DIR/loop.sh"
CACHE_DIR="$ROOT_DIR/cache/council"
TOPICS_FILE="$ROOT_DIR/projects/council/research-topics.md"

echo "=== Research Council ==="
echo "Started: $(date)"

# Clean previous outputs
mkdir -p "$CACHE_DIR"
rm -f "$CACHE_DIR"/research-output-*.md

# --- Phase 1: Research ---
# Each research task gets a narrow manifest with only its topic
# These could run in parallel with & and wait, but serial is safer for now

echo ""
echo "--- Research Phase ---"

# Write research tasks from the shared council topic list.
# Each task file tells the subagent what to research.
if [[ -f "$TOPICS_FILE" ]] && grep -Eq '^[[:space:]]*[-*]?[[:space:]]*[[:alnum:]]' "$TOPICS_FILE"; then
  TASK_NUM=1
  while IFS= read -r topic; do
    [[ "$topic" =~ ^#.*$ ]] && continue
    [[ -z "$topic" ]] && continue
    topic=$(printf '%s\n' "$topic" | sed 's/^[[:space:]]*[-*]\?[[:space:]]*//')
    [[ -n "$topic" ]] || continue

    echo "Researching: $topic"

    # Write task file for this subagent
    printf '# Research Task\n\nResearch the following topic and report findings:\n\n%s\n' "$topic" \
      > "$CACHE_DIR/current-research-task.md"

    # Run research subagent with narrow manifest, max 3 iterations
    "$LOOP" "$ROOT_DIR/manifests/council-research.md" 3 \
      > "$CACHE_DIR/research-output-${TASK_NUM}.md" 2>&1 || true

    TASK_NUM=$((TASK_NUM + 1))
  done < "$TOPICS_FILE"
else
  echo "No research topics queued in $TOPICS_FILE"
  echo "Add one topic per line, then rerun ./scripts/run-council.sh"
  exit 1
fi

# --- Phase 2: Synthesis ---
echo ""
echo "--- Synthesis Phase ---"

# Synthesis agent gets research outputs + priorities, produces final summary
"$LOOP" "$ROOT_DIR/manifests/council-synthesis.md" 3 \
  > "$CACHE_DIR/council-summary.md" 2>&1 || true

echo ""
echo "=== Council Complete ==="
echo "Summary: $CACHE_DIR/council-summary.md"
echo "Finished: $(date)"