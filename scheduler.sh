#!/usr/bin/env bash
# scheduler.sh — Scheduled task runner with quiet-hours support

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/config.yaml"
RUN_PROJECT="$SCRIPT_DIR/run-project.sh"
DEMOTE_SCRIPT="$SCRIPT_DIR/memory-engine/demote.py"
PROMOTE_SCRIPT="$SCRIPT_DIR/memory-engine/promoter.py"
LOG_FILE="$SCRIPT_DIR/logs/scheduler.log"
CRON_TAG="# agent-loop"

mkdir -p "$SCRIPT_DIR/logs"

log_scheduler() {
  printf '%s %s\n' "$(date +"%Y-%m-%d %H:%M:%S")" "$1" | tee -a "$LOG_FILE"
}

task_ignores_quiet_hours() {
  case "${1:-}" in
    demotion|promotion)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

get_schedule_value() {
  local key="$1"
  awk -v target="$key" '
    /^schedule:/ { in_schedule = 1; next }
    in_schedule && /^[^[:space:]]/ { in_schedule = 0 }
    in_schedule && $0 ~ "^[[:space:]]{2}" target ":" {
      line = $0
      sub("^[[:space:]]{2}" target ":[[:space:]]*", "", line)
      sub(/[[:space:]]*#.*/, "", line)
      gsub(/"/, "", line)
      print line
      exit
    }
  ' "$CONFIG"
}

run_with_scheduled_llm() {
  local scheduled_model_id scheduled_api_base
  scheduled_model_id=$(get_schedule_value "llm_model_id")
  scheduled_api_base=$(get_schedule_value "llm_api_base")

  if [[ -n "$scheduled_model_id" && -n "$scheduled_api_base" ]]; then
    AGENT_LOOP_MODEL_ID="$scheduled_model_id" AGENT_LOOP_API_BASE="$scheduled_api_base" "$@"
  elif [[ -n "$scheduled_model_id" ]]; then
    AGENT_LOOP_MODEL_ID="$scheduled_model_id" "$@"
  elif [[ -n "$scheduled_api_base" ]]; then
    AGENT_LOOP_API_BASE="$scheduled_api_base" "$@"
  else
    "$@"
  fi
}

list_scheduled_tasks() {
  awk '
    /^schedule:/ { in_schedule = 1; next }
    in_schedule && /^[^[:space:]]/ { in_schedule = 0 }
    in_schedule && /^[[:space:]]{2}tasks:/ { in_tasks = 1; next }
    in_tasks && /^[[:space:]]{2}[A-Za-z0-9_-]+:/ { in_tasks = 0 }
    in_tasks && /^[[:space:]]{4}[A-Za-z0-9._-]+:/ {
      line = $0
      sub(/^[[:space:]]{4}/, "", line)
      name = line
      sub(/:.*/, "", name)
      cron = line
      sub(/^[^:]+:[[:space:]]*/, "", cron)
      sub(/[[:space:]]*#.*/, "", cron)
      gsub(/"/, "", cron)
      print name "|" cron
    }
  ' "$CONFIG"
}

time_to_minutes() {
  awk -F: -v value="${1:-00:00}" 'BEGIN {
    split(value, parts, ":")
    hour = parts[1] + 0
    minute = parts[2] + 0
    print (hour * 60) + minute
  }'
}

current_minutes_local() {
  local now_h now_m
  now_h=$(date +"%H")
  now_m=$(date +"%M")
  awk -v hour="$now_h" -v minute="$now_m" 'BEGIN { print (hour * 60) + minute }'
}

is_quiet_hours() {
  local quiet_start quiet_end now_minutes start_minutes end_minutes

  quiet_start=$(get_schedule_value "quiet_hours_start")
  quiet_end=$(get_schedule_value "quiet_hours_end")

  [[ -n "$quiet_start" ]] || return 1
  [[ -n "$quiet_end" ]] || return 1

  now_minutes=$(current_minutes_local)
  start_minutes=$(time_to_minutes "$quiet_start")
  end_minutes=$(time_to_minutes "$quiet_end")

  if [[ "$start_minutes" == "$end_minutes" ]]; then
    return 1
  fi

  if (( start_minutes < end_minutes )); then
    (( now_minutes >= start_minutes && now_minutes < end_minutes ))
  else
    (( now_minutes >= start_minutes || now_minutes < end_minutes ))
  fi
}

last_run_for_task() {
  local task_name="$1"

  if [[ ! -f "$LOG_FILE" ]]; then
    printf '%s\n' "never"
    return 0
  fi

  awk -v task="$task_name" '
    index($0, " task=" task " ") > 0 {
      last = $0
    }
    END {
      if (last != "") {
        print last
      } else {
        print "never"
      }
    }
  ' "$LOG_FILE"
}

run_task() {
  local task_name="$1"
  local force_run="${2:-false}"
  local scheduled_model_id
  scheduled_model_id=$(get_schedule_value "llm_model_id")

  if [[ -z "$task_name" ]]; then
    printf '%s\n' "Usage: ./scheduler.sh run <task> [--force]" >&2
    exit 1
  fi

  if [[ "$force_run" != "true" ]] && ! task_ignores_quiet_hours "$task_name" && is_quiet_hours; then
    log_scheduler "[scheduler] SKIP task=${task_name} reason=quiet_hours"
    printf '[scheduler] Quiet hours active. Skipping %s.\n' "$task_name"
    exit 0
  fi

  log_scheduler "[scheduler] RUN task=${task_name} force=${force_run} model=${scheduled_model_id:-default}"
  case "$task_name" in
    demotion)
      python3 "$DEMOTE_SCRIPT"
      ;;
    promotion)
      run_with_scheduled_llm python3 "$PROMOTE_SCRIPT"
      ;;
    *)
      run_with_scheduled_llm bash "$RUN_PROJECT" "$task_name"
      ;;
  esac
}

show_status() {
  local quiet_start quiet_end timezone status_line tasks_output scheduled_model_id scheduled_api_base

  quiet_start=$(get_schedule_value "quiet_hours_start")
  quiet_end=$(get_schedule_value "quiet_hours_end")
  timezone=$(get_schedule_value "timezone")
  scheduled_model_id=$(get_schedule_value "llm_model_id")
  scheduled_api_base=$(get_schedule_value "llm_api_base")

  printf 'Current time: %s\n' "$(date +"%Y-%m-%d %H:%M:%S")"
  if [[ -n "$timezone" ]]; then
    printf 'Configured timezone: %s\n' "$timezone"
  fi
  if [[ -n "$scheduled_model_id" ]]; then
    printf 'Scheduled LLM model: %s\n' "$scheduled_model_id"
  fi
  if [[ -n "$scheduled_api_base" ]]; then
    printf 'Scheduled LLM API base: %s\n' "$scheduled_api_base"
  fi

  if is_quiet_hours; then
    status_line="active"
  else
    status_line="inactive"
  fi

  if [[ -n "$quiet_start" && -n "$quiet_end" ]]; then
    printf 'Quiet hours: %s (%s to %s)\n' "$status_line" "$quiet_start" "$quiet_end"
  else
    printf 'Quiet hours: not configured\n'
  fi

  printf '\nScheduled tasks:\n'
  tasks_output=$(list_scheduled_tasks)
  if [[ -z "$tasks_output" ]]; then
    printf '  none configured\n'
    return 0
  fi

  while IFS='|' read -r name cron_expr; do
    [[ -n "$name" ]] || continue
    printf '  %s -> %s\n' "$name" "$cron_expr"
    printf '    last run: %s\n' "$(last_run_for_task "$name")"
  done <<EOF
$tasks_output
EOF
}

generate_cron_entries() {
  local timezone tasks_output

  timezone=$(get_schedule_value "timezone")
  tasks_output=$(list_scheduled_tasks)

  while IFS='|' read -r name cron_expr; do
    [[ -n "$name" && -n "$cron_expr" ]] || continue
    if [[ -n "$timezone" ]]; then
      printf '%s cd "%s" && TZ="%s" "%s/scheduler.sh" run "%s" %s\n' "$cron_expr" "$SCRIPT_DIR" "$timezone" "$SCRIPT_DIR" "$name" "$CRON_TAG"
    else
      printf '%s cd "%s" && "%s/scheduler.sh" run "%s" %s\n' "$cron_expr" "$SCRIPT_DIR" "$SCRIPT_DIR" "$name" "$CRON_TAG"
    fi
  done <<EOF
$tasks_output
EOF
}

cron_install() {
  local existing_cron new_cron

  existing_cron=$(crontab -l 2>/dev/null | awk -v tag="$CRON_TAG" 'index($0, tag) == 0 { print }' || true)
  new_cron=$(generate_cron_entries)

  {
    if [[ -n "$existing_cron" ]]; then
      printf '%s\n' "$existing_cron"
    fi
    if [[ -n "$new_cron" ]]; then
      printf '%s\n' "$new_cron"
    fi
  } | crontab -

  printf '%s\n' "Installed agent-loop cron entries."
}

cron_remove() {
  local cleaned_cron

  cleaned_cron=$(crontab -l 2>/dev/null | awk -v tag="$CRON_TAG" 'index($0, tag) == 0 { print }' || true)
  printf '%s\n' "$cleaned_cron" | crontab -
  printf '%s\n' "Removed agent-loop cron entries."
}

COMMAND="${1:-status}"
shift || true

case "$COMMAND" in
  run)
    PROJECT_NAME="${1:-}"
    shift || true
    FORCE="false"
    if [[ "${1:-}" == "--force" ]]; then
      FORCE="true"
    fi
    run_task "$PROJECT_NAME" "$FORCE"
    ;;
  status)
    show_status
    ;;
  cron-install)
    cron_install
    ;;
  cron-remove)
    cron_remove
    ;;
  *)
    printf '%s\n' "Usage: ./scheduler.sh {run <project> [--force]|status|cron-install|cron-remove}" >&2
    exit 1
    ;;
esac
