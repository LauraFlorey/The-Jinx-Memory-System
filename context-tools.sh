#!/usr/bin/env bash
# context-tools.sh
# Shared helpers for context budgeting in the agent loop framework.
# This file is sourced by loop.sh to estimate tokens, compress markdown
# before prompt injection, and track prompt budget usage by category.

_chars_to_tokens() {
  awk -v chars="${1:-0}" 'BEGIN {
    if (chars <= 0) {
      print 0
    } else {
      print int((chars + 3) / 4)
    }
  }'
}

_budget_int_add() {
  awk -v left="${1:-0}" -v right="${2:-0}" 'BEGIN {
    print int(left + right)
  }'
}

_budget_usable_tokens() {
  awk -v max="${BUDGET_MAX_TOKENS:-0}" -v reserved="${BUDGET_RESERVED_FOR_RESPONSE:-0}" 'BEGIN {
    usable = max - reserved
    if (usable < 0) {
      usable = 0
    }
    print int(usable)
  }'
}

estimate_tokens() {
  local text chars

  text="${1-}"
  chars=$(printf '%s' "$text" | wc -c | awk '{print $1 + 0}')
  _chars_to_tokens "$chars"
}

estimate_file_tokens() {
  local filepath chars

  filepath="${1-}"
  if [ -z "$filepath" ] || [ ! -f "$filepath" ]; then
    echo "0"
    return 0
  fi

  chars=$(wc -c < "$filepath" | awk '{print $1 + 0}')
  _chars_to_tokens "$chars"
}

compress_context() {
  local text

  text="${1-}"
  printf '%s\n' "$text" | awk '
    function trim(value) {
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      return value
    }

    {
      line = $0

      if (line ~ /^[[:space:]]*([*-][[:space:]]*){3,}$/) {
        next
      }

      sub(/^[[:space:]]*#{1,6}[[:space:]]*/, "", line)
      sub(/^[[:space:]]*[-+*][[:space:]]+/, "", line)

      gsub(/\*\*/, "", line)
      gsub(/__/, "", line)
      gsub(/\*/, "", line)

      gsub(/[[:space:]]+/, " ", line)
      line = trim(line)

      if (line == "") {
        if (seen_text && !pending_blank) {
          pending_blank = 1
        }
        next
      }

      if (pending_blank) {
        print ""
        pending_blank = 0
      }

      print line
      seen_text = 1
    }
  '
}

init_budget() {
  BUDGET_MAX_TOKENS="${1:-0}"
  BUDGET_RESERVED_FOR_RESPONSE=4096
  BUDGET_USED=0
  BUDGET_HOT=0
  BUDGET_MANIFEST=0
  BUDGET_STATE=0
  BUDGET_FILES_LOADED=""
  BUDGET_FILES_SKIPPED=""
}

check_budget() {
  local additional usable fits

  additional="${1:-0}"
  usable=$(_budget_usable_tokens)
  fits=$(awk -v used="${BUDGET_USED:-0}" -v add="$additional" -v usable="$usable" 'BEGIN {
    print ((used + add) <= usable) ? 1 : 0
  }')

  [ "$fits" = "1" ]
}

add_to_budget() {
  local tokens category name entry

  tokens="${1:-0}"
  category="${2:-unknown}"
  name="${3:-unnamed}"

  BUDGET_USED=$(_budget_int_add "${BUDGET_USED:-0}" "$tokens")

  case "$category" in
    hot)
      BUDGET_HOT=$(_budget_int_add "${BUDGET_HOT:-0}" "$tokens")
      ;;
    manifest)
      BUDGET_MANIFEST=$(_budget_int_add "${BUDGET_MANIFEST:-0}" "$tokens")
      ;;
    state)
      BUDGET_STATE=$(_budget_int_add "${BUDGET_STATE:-0}" "$tokens")
      ;;
  esac

  entry="${category}|${name}|${tokens}"
  if [ -n "${BUDGET_FILES_LOADED:-}" ]; then
    BUDGET_FILES_LOADED="${BUDGET_FILES_LOADED}
${entry}"
  else
    BUDGET_FILES_LOADED="${entry}"
  fi
}

get_budget_percent() {
  awk -v used="${BUDGET_USED:-0}" -v max="${BUDGET_MAX_TOKENS:-0}" -v reserved="${BUDGET_RESERVED_FOR_RESPONSE:-0}" 'BEGIN {
    usable = max - reserved
    if (usable <= 0) {
      if (used > 0) {
        print 100
      } else {
        print 0
      }
      exit
    }

    pct = int((used / usable) * 100)
    if (pct < 0) {
      pct = 0
    }
    if (pct > 100) {
      pct = 100
    }
    print pct
  }'
}

get_budget_percent_display() {
  awk -v used="${BUDGET_USED:-0}" -v max="${BUDGET_MAX_TOKENS:-0}" -v reserved="${BUDGET_RESERVED_FOR_RESPONSE:-0}" 'BEGIN {
    usable = max - reserved
    if (usable <= 0) {
      if (used > 0) {
        printf "%.1f", 100
      } else {
        printf "%.1f", 0
      }
      exit
    }

    pct = (used / usable) * 100
    if (pct < 0) {
      pct = 0
    }
    if (pct > 100) {
      pct = 100
    }
    printf "%.1f", pct
  }'
}

check_threshold() {
  local warn_pct rotate_pct threshold_state

  warn_pct="${1:-0.70}"
  rotate_pct="${2:-0.80}"
  threshold_state=$(awk -v used="${BUDGET_USED:-0}" -v max="${BUDGET_MAX_TOKENS:-0}" -v reserved="${BUDGET_RESERVED_FOR_RESPONSE:-0}" -v warn="$warn_pct" -v rotate="$rotate_pct" 'BEGIN {
    usable = max - reserved
    if (usable <= 0) {
      ratio = 1
    } else {
      ratio = used / usable
    }

    if (ratio >= rotate) {
      print "rotate"
    } else if (ratio >= warn) {
      print "warn"
    } else {
      print "ok"
    }
  }')

  echo "$threshold_state"
}

budget_report() {
  local usable remaining percent

  usable=$(_budget_usable_tokens)
  remaining=$(awk -v usable="$usable" -v used="${BUDGET_USED:-0}" 'BEGIN {
    left = usable - used
    if (left < 0) {
      left = 0
    }
    print int(left)
  }')
  percent=$(get_budget_percent_display)

  printf '%s\n' "Context Budget Report"
  printf '%s\n' "Max tokens: ${BUDGET_MAX_TOKENS:-0}"
  printf '%s\n' "Reserved for response: ${BUDGET_RESERVED_FOR_RESPONSE:-0}"
  printf '%s\n' "Usable prompt budget: ${usable}"
  printf '%s\n' "Used: ${BUDGET_USED:-0} (${percent}%%)"
  printf '%s\n' "Remaining: ${remaining}"
  printf '%s\n' "Category totals:"
  printf '%s\n' "  hot: ${BUDGET_HOT:-0}"
  printf '%s\n' "  manifest: ${BUDGET_MANIFEST:-0}"
  printf '%s\n' "  state: ${BUDGET_STATE:-0}"
  printf '%s\n' "Loaded files:"

  if [ -n "${BUDGET_FILES_LOADED:-}" ]; then
    while IFS='|' read -r category name tokens; do
      [ -n "${category}${name}${tokens}" ] || continue
      printf '%s\n' "  - [${category}] ${name}: ${tokens} tokens"
    done <<EOF
${BUDGET_FILES_LOADED}
EOF
  else
    printf '%s\n' "  - none"
  fi

  printf '%s\n' "Skipped files:"
  if [ -n "${BUDGET_FILES_SKIPPED:-}" ]; then
    while IFS= read -r skipped; do
      [ -n "$skipped" ] || continue
      printf '%s\n' "  - ${skipped}"
    done <<EOF
${BUDGET_FILES_SKIPPED}
EOF
  else
    printf '%s\n' "  - none"
  fi
}
