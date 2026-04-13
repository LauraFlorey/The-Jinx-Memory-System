#!/usr/bin/env bash
# agent-loop.sh — Outer loop for stateless agent with filesystem memory
# Phase 1: Clean context rotation, manifest-based loading, session handoff
#
# Usage:
#   ./loop.sh                          # run with default manifest
#   ./loop.sh manifests/brief.md       # run with specific manifest
#   ./loop.sh manifests/research.md 5  # run with max 5 iterations

set -euo pipefail

# --- Configuration ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/config.yaml"
source "$SCRIPT_DIR/context-tools.sh"
source "$SCRIPT_DIR/beliefs-tools.sh"

# Parse config (lightweight — no yq dependency, just grep/sed)
get_config() {
  local key="$1"
  local default="${2:-}"
  local value
  # Match key at start, then grab everything after the FIRST ": " (preserves URLs)
  value=$(grep "^  ${key}:" "$CONFIG" 2>/dev/null | sed "s/^  ${key}: *//" | sed 's/ *#.*//' | tr -d '"' || true)
  echo "${value:-$default}"
}

# Core settings
MODEL_ID="${AGENT_LOOP_MODEL_ID:-$(get_config "model_id" "anthropic/claude-sonnet-4.6")}"
API_BASE="${AGENT_LOOP_API_BASE:-$(get_config "api_base" "https://openrouter.ai/api/v1")}"
MAX_ITERATIONS=$(get_config "max_iterations" "20")
MAX_ROTATIONS=$(get_config "max_rotations" "5")
MAX_COST=$(get_config "max_cost_usd" "2.00")
COOLDOWN=$(get_config "cooldown_seconds" "5")
COMPLETION_SIGNAL=$(get_config "completion_signal" "SESSION_COMPLETE")
HOT_DIR=$(get_config "hot_dir" "memory/hot")
MAX_CONTEXT_TOKENS=$(get_config "max_tokens" "128000")
HOT_MEMORY_CAP=$(get_config "hot_memory_cap" "4000")
WARN_THRESHOLD=$(get_config "warn_threshold" "0.70")
ROTATE_THRESHOLD=$(get_config "rotate_threshold" "0.80")

resolve_manifest_path() {
  local manifest_path="$1"

  if [[ "$manifest_path" == /* ]]; then
    echo "$manifest_path"
  else
    echo "$SCRIPT_DIR/$manifest_path"
  fi
}

detect_project_name() {
  local manifest_path="$1"
  local manifest_name

  manifest_name=$(basename "$manifest_path" .md)
  if [[ -z "$manifest_name" ]] || [[ "$manifest_name" == "default" ]] || [[ "$manifest_name" == "$(basename "$manifest_path")" ]]; then
    echo ""
  else
    echo "$manifest_name"
  fi
}

sanitize_project_name() {
  printf '%s' "${1:-}" | sed 's#[/[:space:]]\+#-#g; s#[^A-Za-z0-9._-]#-#g'
}

legacy_state_key() {
  local manifest_path="$1"
  local key="$manifest_path"

  if [[ "$key" == "$SCRIPT_DIR/"* ]]; then
    key="${key#$SCRIPT_DIR/}"
  fi
  key="${key#./}"
  key="${key%.md}"
  key=$(printf '%s' "$key" | sed 's#[/[:space:]]\+#-#g; s#[^A-Za-z0-9._-]#-#g')
  echo "${key:-default}"
}

resolve_legacy_state_dir() {
  local manifest_path="$1"
  local relative_manifest key_single key_double

  relative_manifest="$manifest_path"
  if [[ "$relative_manifest" == "$SCRIPT_DIR/"* ]]; then
    relative_manifest="${relative_manifest#$SCRIPT_DIR/}"
  fi
  relative_manifest="${relative_manifest#./}"
  relative_manifest="${relative_manifest%.md}"

  key_single=$(printf '%s' "$relative_manifest" | sed 's#[/[:space:]]\+#-#g; s#[^A-Za-z0-9._-]#-#g')
  key_double=$(printf '%s' "$relative_manifest" | sed 's#[/[:space:]]\+#--#g; s#[^A-Za-z0-9._-]#-#g')

  if [[ -d "$LEGACY_STATE_ROOT/$key_single" ]]; then
    printf '%s\n' "$LEGACY_STATE_ROOT/$key_single"
  elif [[ -d "$LEGACY_STATE_ROOT/$key_double" ]]; then
    printf '%s\n' "$LEGACY_STATE_ROOT/$key_double"
  else
    printf '%s\n' "$LEGACY_STATE_ROOT/$key_single"
  fi
}

# CLI overrides
PROJECT_NAME=""
POSITIONAL_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      if [[ $# -lt 2 ]] || [[ -z "${2:-}" ]]; then
        echo "[error] --project requires a value" >&2
        exit 1
      fi
      PROJECT_NAME="$2"
      shift 2
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

MANIFEST_INPUT="${POSITIONAL_ARGS[0]:-manifests/default.md}"
MANIFEST=$(resolve_manifest_path "$MANIFEST_INPUT")
if [[ ${#POSITIONAL_ARGS[@]} -ge 2 ]]; then
  MAX_ITERATIONS="${POSITIONAL_ARGS[1]}"
fi
if [[ -z "$PROJECT_NAME" ]]; then
  PROJECT_NAME=$(detect_project_name "$MANIFEST")
fi

PROJECT_SLUG=$(sanitize_project_name "$PROJECT_NAME")
LEGACY_STATE_ROOT="$SCRIPT_DIR/state/manifests"
LEGACY_STATE_KEY=$(legacy_state_key "$MANIFEST")
LEGACY_STATE_DIR=$(resolve_legacy_state_dir "$MANIFEST")

# --- State ---
ITERATION=0
ROTATIONS=0
TOTAL_COST=0.0
SESSION_ID=$(date +%Y%m%d-%H%M%S)
if [[ -n "$PROJECT_SLUG" ]]; then
  SESSION_LOG="$SCRIPT_DIR/logs/session-${PROJECT_SLUG}-${SESSION_ID}.log"
  STATE_DIR="$SCRIPT_DIR/state/$PROJECT_SLUG"
else
  SESSION_LOG="$SCRIPT_DIR/logs/session-${SESSION_ID}.log"
  STATE_DIR="$SCRIPT_DIR/state"
fi
PROGRESS="$STATE_DIR/progress.md"
BELIEFS="$STATE_DIR/beliefs.md"
SESSION_SUMMARY="$STATE_DIR/session-summary.md"
LEGACY_PROGRESS="$LEGACY_STATE_DIR/progress.md"
LEGACY_SESSION_SUMMARY="$LEGACY_STATE_DIR/session-summary.md"
LEGACY_BELIEFS="$SCRIPT_DIR/state/beliefs.md"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- Logging ---
log() { echo -e "${BLUE}[loop]${NC} $1" | tee -a "$SESSION_LOG"; }
warn() { echo -e "${YELLOW}[warn]${NC} $1" | tee -a "$SESSION_LOG"; }
error() { echo -e "${RED}[error]${NC} $1" | tee -a "$SESSION_LOG"; }
success() { echo -e "${GREEN}[done]${NC} $1" | tee -a "$SESSION_LOG"; }

budget_entry_count() {
  printf '%s\n' "${1:-}" | awk 'NF { count++ } END { print count + 0 }'
}

format_loaded_budget_entries() {
  if [[ -z "${1:-}" ]]; then
    printf '%s\n' "- none"
    return 0
  fi

  printf '%s\n' "$1" | awk -F'|' '
    NF >= 3 {
      printf "- [%s] %s: %s tokens\n", $1, $2, $3
      found = 1
    }
    END {
      if (!found) {
        print "- none"
      }
    }
  '
}

format_skipped_budget_entries() {
  if [[ -z "${1:-}" ]]; then
    printf '%s\n' "- none"
    return 0
  fi

  printf '%s\n' "$1" | awk '
    NF {
      printf "- %s\n", $0
      found = 1
    }
    END {
      if (!found) {
        print "- none"
      }
    }
  '
}

process_belief_reports() {
  local iteration_output="$1"
  local line payload title what_happened why what_i_learned

  while IFS= read -r line; do
    [[ "$line" == BELIEF:* ]] || continue

    payload="${line#BELIEF:}"
    IFS='|' read -r title what_happened why what_i_learned <<EOF
$payload
EOF

    title=$(printf '%s' "${title:-}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
    what_happened=$(printf '%s' "${what_happened:-}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
    why=$(printf '%s' "${why:-}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
    what_i_learned=$(printf '%s' "${what_i_learned:-}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')

    if [[ -z "$title" || -z "$what_happened" || -z "$why" || -z "$what_i_learned" ]]; then
      warn "Skipping malformed belief report: $line"
      continue
    fi

    if add_belief "$title" "$what_happened" "$why" "$what_i_learned"; then
      log "Belief added: $title"
    else
      warn "Failed to record belief: $title"
    fi
  done <<EOF
$iteration_output
EOF
}

# --- Init ---
init() {
  mkdir -p "$SCRIPT_DIR"/{logs,state,cache,memory/{hot,warm,cold},projects,manifests,prompts}
  mkdir -p "$STATE_DIR"

  if [[ "$PROGRESS" != "$LEGACY_PROGRESS" ]] && [[ ! -f "$PROGRESS" ]] && [[ -f "$LEGACY_PROGRESS" ]]; then
    cp "$LEGACY_PROGRESS" "$PROGRESS"
    log "Imported legacy progress from $LEGACY_PROGRESS"
  fi

  if [[ "$SESSION_SUMMARY" != "$LEGACY_SESSION_SUMMARY" ]] && [[ ! -f "$SESSION_SUMMARY" ]] && [[ -f "$LEGACY_SESSION_SUMMARY" ]]; then
    cp "$LEGACY_SESSION_SUMMARY" "$SESSION_SUMMARY"
    log "Imported legacy session summary from $LEGACY_SESSION_SUMMARY"
  fi

  if [[ "$BELIEFS" != "$LEGACY_BELIEFS" ]] && [[ ! -f "$BELIEFS" ]] && [[ -f "$LEGACY_BELIEFS" ]]; then
    cp "$LEGACY_BELIEFS" "$BELIEFS"
    log "Imported legacy beliefs from $LEGACY_BELIEFS"
  fi

  # Initialize state files if they don't exist
  [[ -f "$PROGRESS" ]] || echo $'# Progress\n\nNo sessions yet.' > "$PROGRESS"
  [[ -f "$BELIEFS" ]] || cat > "$BELIEFS" <<'EOF'
# Beliefs

## What I've Learned
Beliefs earned through experience. Some are about avoiding failure.
Some are about what makes work good. All are mine.
EOF
   
  # Verify API key
  if [[ -z "${AGENT_LOOP_API_KEY:-}" ]]; then
    error "AGENT_LOOP_API_KEY not set. Export it and try again."
    exit 1
  fi

  # Verify manifest exists
  if [[ ! -f "$MANIFEST" ]]; then
    error "Manifest not found: $MANIFEST"
    exit 1
  fi

  log "Session $SESSION_ID starting"
  log "Model: $MODEL_ID"
  log "Manifest: $MANIFEST"
  if [[ -n "$PROJECT_NAME" ]]; then
    log "Project: $PROJECT_NAME"
  fi
  log "State directory: $STATE_DIR"
  log "Max iterations: $MAX_ITERATIONS"
  log "Max rotations: $MAX_ROTATIONS"
  log "Max cost: \$${MAX_COST}"
}

load_fresh_context() {
  local context_file

  context_file=$(mktemp)
  assemble_context > "$context_file"
  TASK_PROMPT=$(cat "$context_file")
  rm -f "$context_file"
}

log_context_status() {
  local loaded_count skipped_count

  loaded_count=$(budget_entry_count "${BUDGET_FILES_LOADED:-}")
  skipped_count=$(budget_entry_count "${BUDGET_FILES_SKIPPED:-}")
  log "Context: $(get_budget_percent_display)% used ($BUDGET_USED/$BUDGET_MAX_TOKENS tokens), ${loaded_count} files loaded, ${skipped_count} skipped"
}

# --- Context Assembly ---
# Reads the manifest file and assembles context from referenced files
assemble_context() {
  local context=""
  local content compressed tokens filename hot_total threshold_status budget_summary skipped_count usable_budget

  init_budget "$MAX_CONTEXT_TOKENS"

  # 1. Load hot memory (always loaded)
  if [[ -d "$SCRIPT_DIR/$HOT_DIR" ]]; then
    for f in "$SCRIPT_DIR/$HOT_DIR"/*.md; do
      [[ -f "$f" ]] || continue
      filename=$(basename "$f")
      content=$(cat "$f")
      compressed=$(compress_context "$content")
      tokens=$(estimate_tokens "$compressed")
      hot_total=$(awk -v current="${BUDGET_HOT:-0}" -v add="$tokens" 'BEGIN { print int(current + add) }')

      if awk -v total="$hot_total" -v cap="$HOT_MEMORY_CAP" 'BEGIN { exit (total > cap) ? 0 : 1 }'; then
        if [[ -n "${BUDGET_FILES_SKIPPED:-}" ]]; then
          BUDGET_FILES_SKIPPED="${BUDGET_FILES_SKIPPED}
hot:${filename}"
        else
          BUDGET_FILES_SKIPPED="hot:${filename}"
        fi
        warn "Skipping hot memory file over cap: $filename (${tokens} tokens, hot cap ${HOT_MEMORY_CAP})" >&2
        continue
      fi

      context+="
--- HOT MEMORY: ${filename} ---
${compressed}
"
      add_to_budget "$tokens" "hot" "$filename"
    done
  fi

  # 2. Load manifest-specified files
  while IFS= read -r line; do
    # Skip comments and empty lines
    [[ "$line" =~ ^#.*$ ]] && continue
    [[ -z "$line" ]] && continue

    local filepath="$SCRIPT_DIR/$line"
    if [[ -f "$filepath" ]]; then
      filename=$(basename "$filepath")
      content=$(cat "$filepath")
      compressed=$(compress_context "$content")
      tokens=$(estimate_tokens "$compressed")

      if ! check_budget "$tokens"; then
        if [[ -n "${BUDGET_FILES_SKIPPED:-}" ]]; then
          BUDGET_FILES_SKIPPED="${BUDGET_FILES_SKIPPED}
manifest:${filename}"
        else
          BUDGET_FILES_SKIPPED="manifest:${filename}"
        fi
        warn "Skipping manifest file due to context budget: $filename (${tokens} tokens)" >&2
        continue
      fi

      context+="
--- MANIFEST: ${filename} ---
${compressed}
"
      add_to_budget "$tokens" "manifest" "$filename"
    else
      warn "Manifest references missing file: $line" >&2
    fi
  done < "$MANIFEST"

  # 3. Load session state
  if [[ -f "$SESSION_SUMMARY" ]]; then
    content=$(cat "$SESSION_SUMMARY")
    compressed=$(compress_context "$content")
    tokens=$(estimate_tokens "$compressed")
    if check_budget "$tokens"; then
      context+="
--- PREVIOUS SESSION SUMMARY ---
${compressed}
"
      add_to_budget "$tokens" "state" "$(basename "$SESSION_SUMMARY")"
    else
      if [[ -n "${BUDGET_FILES_SKIPPED:-}" ]]; then
        BUDGET_FILES_SKIPPED="${BUDGET_FILES_SKIPPED}
state:$(basename "$SESSION_SUMMARY")"
      else
        BUDGET_FILES_SKIPPED="state:$(basename "$SESSION_SUMMARY")"
      fi
      warn "Skipping state file due to exhausted context budget: $(basename "$SESSION_SUMMARY") (${tokens} tokens)" >&2
    fi
  fi

  if [[ -f "$BELIEFS" ]]; then
    content=$(cat "$BELIEFS")
    compressed=$(compress_context "$content")
    tokens=$(estimate_tokens "$compressed")
    if check_budget "$tokens"; then
      context+="
--- BELIEFS (earned perspectives and lessons) ---
${compressed}
"
      add_to_budget "$tokens" "state" "$(basename "$BELIEFS")"
    else
      if [[ -n "${BUDGET_FILES_SKIPPED:-}" ]]; then
        BUDGET_FILES_SKIPPED="${BUDGET_FILES_SKIPPED}
state:$(basename "$BELIEFS")"
      else
        BUDGET_FILES_SKIPPED="state:$(basename "$BELIEFS")"
      fi
      warn "Skipping state file due to exhausted context budget: $(basename "$BELIEFS") (${tokens} tokens)" >&2
    fi
  fi

  if [[ -f "$PROGRESS" ]]; then
    content=$(cat "$PROGRESS")
    compressed=$(compress_context "$content")
    tokens=$(estimate_tokens "$compressed")
    if check_budget "$tokens"; then
      context+="
--- PROGRESS ---
${compressed}
"
      add_to_budget "$tokens" "state" "$(basename "$PROGRESS")"
    else
      if [[ -n "${BUDGET_FILES_SKIPPED:-}" ]]; then
        BUDGET_FILES_SKIPPED="${BUDGET_FILES_SKIPPED}
state:$(basename "$PROGRESS")"
      else
        BUDGET_FILES_SKIPPED="state:$(basename "$PROGRESS")"
      fi
      warn "Skipping state file due to exhausted context budget: $(basename "$PROGRESS") (${tokens} tokens)" >&2
    fi
  fi

  threshold_status=$(check_threshold "$WARN_THRESHOLD" "$ROTATE_THRESHOLD")
  case "$threshold_status" in
    warn)
      warn "$(budget_report)" >&2
      ;;
    rotate)
      error "Context budget near capacity (${BUDGET_USED}/${MAX_CONTEXT_TOKENS} tokens loaded, $(get_budget_percent)% of usable budget)." >&2
      ;;
  esac

  skipped_count=$(printf '%s\n' "${BUDGET_FILES_SKIPPED:-}" | awk 'NF { count++ } END { print count + 0 }')
  usable_budget=$(awk -v max="$MAX_CONTEXT_TOKENS" -v reserved="${BUDGET_RESERVED_FOR_RESPONSE:-0}" 'BEGIN {
    usable = max - reserved
    if (usable < 0) {
      usable = 0
    }
    print int(usable)
  }')
  budget_summary="Context assembled: used ${BUDGET_USED}/${usable_budget} prompt tokens (${BUDGET_HOT} hot, ${BUDGET_MANIFEST} manifest, ${BUDGET_STATE} state), skipped ${skipped_count} files"
  log "$budget_summary" >&2

  echo "$context"
}

# --- Build System Prompt ---
build_system_prompt() {
  local system_prompt_file="$SCRIPT_DIR/prompts/system.md"
  local local_datetime utc_datetime
  local_datetime=$(date +"%Y-%m-%d %H:%M:%S %Z")
  utc_datetime=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  if [[ -f "$system_prompt_file" ]]; then
    cat "$system_prompt_file"
  else
    echo "You are an AI agent. Complete the task described in your context. When finished, output $COMPLETION_SIGNAL on its own line."
  fi
  printf '\n\nCurrent date/time reference:\n- Local: %s\n- UTC: %s\n' "$local_datetime" "$utc_datetime"
}

# --- Call LLM ---
call_llm() {
  local system_prompt="$1"
  local user_content="$2"
  local response_file="$3"

  # Build JSON payload to a temp file
  local payload_file
  payload_file=$(mktemp)

  jq -n \
    --arg model "$MODEL_ID" \
    --arg system "$system_prompt" \
    --arg user "$user_content" \
    '{
      model: $model,
      messages: [
        {role: "system", content: $system},
        {role: "user", content: $user}
      ],
      max_tokens: 4096
    }' > "$payload_file" 2>>"$SESSION_LOG"

  # Call API, write response to file
  curl -s "$API_BASE/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $AGENT_LOOP_API_KEY" \
    -d @"$payload_file" > "$response_file" 2>>"$SESSION_LOG"

  rm -f "$payload_file"
}

# --- Extract response text ---
extract_content() {
  local response_file="$1"

  # Check for API error
  local api_error
  api_error=$(jq -r '.error.message // empty' < "$response_file" 2>/dev/null)
  if [[ -n "$api_error" ]]; then
    error "API returned error: $api_error"
    echo "ERROR: $api_error"
    return
  fi

  # Extract assistant message
  local content
  content=$(jq -r '.choices[0].message.content // empty' < "$response_file" 2>/dev/null)

  if [[ -z "$content" ]]; then
    warn "Empty response from model"
    warn "Raw response: $(cat "$response_file")"
    echo "ERROR: Empty response"
    return
  fi

  echo "$content"
}

# --- Extract token usage ---
extract_cost() {
  local response_file="$1"
  local prompt_tokens completion_tokens

  prompt_tokens=$(jq -r '.usage.prompt_tokens // 0' < "$response_file" 2>/dev/null || echo "0")
  completion_tokens=$(jq -r '.usage.completion_tokens // 0' < "$response_file" 2>/dev/null || echo "0")
  prompt_tokens="${prompt_tokens:-0}"
  completion_tokens="${completion_tokens:-0}"

  awk "BEGIN {printf \"%.6f\", $prompt_tokens * 0.0000005 + $completion_tokens * 0.000001}" 2>/dev/null || echo "0"
}

# --- Write session summary ---
write_session_summary() {
  local last_output="$1"
  local reason="$2"
  local continue_from_here=""

  if [[ "$reason" == "context_rotation" ]]; then
    continue_from_here=$(cat <<EOF
## CONTINUE FROM HERE
This session rotated because context grew too large. The task is not complete.
- **Rotation count**: $ROTATIONS
- **Iteration at rotation**: $ITERATION
- **Next action**: Resume from the work captured in "Last Output" above and continue the same task after the fresh context loads.

EOF
)
  fi

  cat > "$SESSION_SUMMARY" <<EOF
# Session Summary
- **Session ID**: $SESSION_ID
- **Completed**: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
- **Iterations**: $ITERATION
- **Rotations**: $ROTATIONS
- **Exit reason**: $reason
- **Estimated cost**: \$${TOTAL_COST}

## Last Output
${last_output:0:2000}

${continue_from_here}## Context Budget
- **Tokens used**: $BUDGET_USED / $BUDGET_MAX_TOKENS ($(get_budget_percent_display)%)
- **Hot memory**: $BUDGET_HOT tokens
- **Manifest files**: $BUDGET_MANIFEST tokens
- **State**: $BUDGET_STATE tokens
- **Files loaded**:
$(format_loaded_budget_entries "${BUDGET_FILES_LOADED:-}")
- **Files skipped**:
$(format_skipped_budget_entries "${BUDGET_FILES_SKIPPED:-}")

## State
- **Project**: ${PROJECT_NAME:-default}
- **State directory**: $STATE_DIR
- **Progress**: $PROGRESS
- **Beliefs**: $BELIEFS
EOF

  log "Session summary written to $SESSION_SUMMARY"
}

rotate_session() {
  local last_output="$1"

  if (( ROTATIONS >= MAX_ROTATIONS )); then
    return 1
  fi

  ROTATIONS=$((ROTATIONS + 1))
  log "Session rotation $ROTATIONS — refreshing context"
  warn "Rotating session because context is near capacity"
  write_session_summary "$last_output" "context_rotation"
  ITERATION=0
  load_fresh_context
  log_context_status
  return 0
}

# --- Main Loop ---
main() {
  init

  local system_prompt
  system_prompt=$(build_system_prompt)

  TASK_PROMPT=""
  load_fresh_context
  log_context_status
  local content=""
  local response_file
  response_file=$(mktemp)

  while (( ITERATION < MAX_ITERATIONS )); do
    ITERATION=$((ITERATION + 1))
    log "--- Iteration $ITERATION / $MAX_ITERATIONS ---"

    # Call LLM — response goes to file, not variable
    call_llm "$system_prompt" "$TASK_PROMPT" "$response_file"

    # Extract content
    content=$(extract_content "$response_file")

    # Log output
    echo "$content" >> "$SESSION_LOG"
    echo "$content"

    # Auto-record beliefs reported by the agent
    process_belief_reports "$content"

    # Cost tracking
    local iter_cost
    iter_cost=$(extract_cost "$response_file")
    TOTAL_COST=$(awk "BEGIN {printf \"%.6f\", $TOTAL_COST + $iter_cost}" 2>/dev/null || echo "$TOTAL_COST")
    log "Iteration cost: \$${iter_cost} | Total: \$${TOTAL_COST}"

    # Check cost cap
    local over_budget
    over_budget=$(awk "BEGIN {print ($TOTAL_COST >= $MAX_COST) ? 1 : 0}" 2>/dev/null || echo "0")
    if [[ "$over_budget" == "1" ]]; then
      warn "Cost cap reached: \$${TOTAL_COST} >= \$${MAX_COST}"
      write_session_summary "$content" "cost_cap"
      rm -f "$response_file"
      exit 0
    fi

    # Check completion only on an exact standalone line
    if echo "$content" | grep -Fxq "$COMPLETION_SIGNAL"; then
      success "Agent signaled completion at iteration $ITERATION"
      write_session_summary "$content" "completed"
      rm -f "$response_file"
      exit 0
    fi

    # Update context for next iteration (append agent output as history)
    TASK_PROMPT="$TASK_PROMPT

--- ITERATION $((ITERATION)) OUTPUT ---
$content

Continue working. If the task is complete, output $COMPLETION_SIGNAL on its own line."

    local current_context_tokens
    current_context_tokens=$(estimate_tokens "$TASK_PROMPT")
    local rotate_limit
    rotate_limit=$(awk "BEGIN {printf \"%d\", $MAX_CONTEXT_TOKENS * $ROTATE_THRESHOLD}" 2>/dev/null || echo "")
    if [[ -z "$rotate_limit" ]] || ! [[ "$rotate_limit" =~ ^[0-9]+$ ]]; then
      warn "Could not calculate context rotate limit (max_tokens=$MAX_CONTEXT_TOKENS, rotate_threshold=$ROTATE_THRESHOLD); skipping iteration growth check"
    elif (( current_context_tokens > rotate_limit )); then
      warn "Context growing too large ($current_context_tokens tokens) — forcing rotation"
      if ! rotate_session "$content"; then
        warn "Max rotations reached ($MAX_ROTATIONS) — stopping session"
        write_session_summary "$content" "max_rotations"
        rm -f "$response_file"
        exit 0
      fi
      sleep "$COOLDOWN"
      continue
    fi

    # Cooldown
    sleep "$COOLDOWN"
  done

  warn "Max iterations reached ($MAX_ITERATIONS)"
  write_session_summary "$content" "max_iterations"
  rm -f "$response_file"
  exit 0
}

main "$@"