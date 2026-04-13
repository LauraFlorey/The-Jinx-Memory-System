#!/usr/bin/env bash
# beliefs-tools.sh
# Shared helpers for managing timestamped belief entries in markdown.
# This file is sourced by loop.sh and operates on the BELIEFS file path.

_beliefs_now_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

_beliefs_file_exists() {
  [ -n "${BELIEFS:-}" ] && [ -f "$BELIEFS" ]
}

_beliefs_ensure_file() {
  if ! _beliefs_file_exists; then
    cat > "$BELIEFS" <<'EOF'
# Beliefs

## What I've Learned
Beliefs earned through experience. Some are about avoiding failure.
Some are about what makes work good. All are mine.
EOF
  fi
}

_beliefs_trim() {
  printf '%s' "${1:-}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

_beliefs_to_epoch() {
  date -j -u -f "%Y-%m-%dT%H:%M:%SZ" "${1:-}" "+%s" 2>/dev/null || echo "0"
}

add_belief() {
  local title what_happened why what_i_learned timestamp

  title=$(_beliefs_trim "${1:-}")
  what_happened=$(_beliefs_trim "${2:-}")
  why=$(_beliefs_trim "${3:-}")
  what_i_learned=$(_beliefs_trim "${4:-}")

  [ -n "$title" ] || return 1
  [ -n "$what_happened" ] || return 1
  [ -n "$why" ] || return 1
  [ -n "$what_i_learned" ] || return 1

  _beliefs_ensure_file
  timestamp=$(_beliefs_now_utc)

  if [ -s "$BELIEFS" ]; then
    printf '\n' >> "$BELIEFS"
  fi

  cat >> "$BELIEFS" <<EOF
<!-- added: $timestamp -->
<!-- hits: 1 -->
<!-- pinned: false -->
### Belief: $title
- **What happened**: $what_happened
- **Why**: $why
- **What I learned**: $what_i_learned
EOF
}

pin_belief() {
  local line_number tmp_file target_start

  line_number="${1:-0}"
  _beliefs_file_exists || return 0
  [ "$line_number" -gt 0 ] 2>/dev/null || return 1

  target_start=$(awk -v target="$line_number" '
    /^<!-- added:/ {
      current_start = NR
    }
    NR <= target && current_start > 0 {
      chosen = current_start
    }
    END {
      print chosen + 0
    }
  ' "$BELIEFS")

  [ "$target_start" -gt 0 ] 2>/dev/null || return 1

  tmp_file=$(mktemp)
  awk -v target_start="$target_start" '
    {
      if (NR == target_start) {
        in_target = 1
      } else if (NR > target_start && /^<!-- added:/) {
        in_target = 0
      }

      if (in_target && /^<!-- pinned:/) {
        print "<!-- pinned: true -->"
      } else {
        print
      }
    }
  ' "$BELIEFS" > "$tmp_file"

  mv "$tmp_file" "$BELIEFS"
}

list_beliefs() {
  local now_epoch

  _beliefs_file_exists || return 0
  now_epoch=$(_beliefs_to_epoch "$(_beliefs_now_utc)")

  awk -v now_epoch="$now_epoch" '
    function trim(value) {
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      return value
    }

    function added_epoch(value, cmd, result) {
      cmd = "date -j -u -f \"%Y-%m-%dT%H:%M:%SZ\" \"" value "\" \"+%s\" 2>/dev/null"
      cmd | getline result
      close(cmd)
      return result + 0
    }

    function print_entry(   age_days, epoch) {
      if (title == "") {
        return
      }

      epoch = added_epoch(added)
      if (epoch > 0 && now_epoch > 0) {
        age_days = int((now_epoch - epoch) / 86400)
        if (age_days < 0) {
          age_days = 0
        }
      } else {
        age_days = 0
      }

      printf "%-36s %-8s %-6s %-6s\n", title, age_days, hits, pinned
    }

    BEGIN {
      printf "%-36s %-8s %-6s %-6s\n", "TITLE", "AGE_DAYS", "HITS", "PINNED"
    }

    /^<!-- added:/ {
      print_entry()
      added = $0
      sub(/^<!-- added:[[:space:]]*/, "", added)
      sub(/[[:space:]]*-->$/, "", added)
      title = ""
      hits = "1"
      pinned = "false"
      next
    }

    /^<!-- hits:/ {
      hits = $0
      sub(/^<!-- hits:[[:space:]]*/, "", hits)
      sub(/[[:space:]]*-->$/, "", hits)
      next
    }

    /^<!-- pinned:/ {
      pinned = $0
      sub(/^<!-- pinned:[[:space:]]*/, "", pinned)
      sub(/[[:space:]]*-->$/, "", pinned)
      next
    }

    /^### Belief:/ {
      title = $0
      sub(/^### Belief:[[:space:]]*/, "", title)
      title = trim(title)
      next
    }

    END {
      print_entry()
    }
  ' "$BELIEFS"
}

prune_beliefs() {
  local max_age_days now_epoch tmp_file

  max_age_days="${1:-0}"
  _beliefs_file_exists || return 0
  [ "$max_age_days" -ge 0 ] 2>/dev/null || return 1

  now_epoch=$(_beliefs_to_epoch "$(_beliefs_now_utc)")
  tmp_file=$(mktemp)

  awk -v max_age_days="$max_age_days" -v now_epoch="$now_epoch" '
    function added_epoch(value, cmd, result) {
      cmd = "date -j -u -f \"%Y-%m-%dT%H:%M:%SZ\" \"" value "\" \"+%s\" 2>/dev/null"
      cmd | getline result
      close(cmd)
      return result + 0
    }

    function flush_entry(   keep, age_days, epoch) {
      if (entry == "") {
        return
      }

      epoch = added_epoch(added)
      if (epoch > 0 && now_epoch > 0) {
        age_days = int((now_epoch - epoch) / 86400)
        if (age_days < 0) {
          age_days = 0
        }
      } else {
        age_days = 0
      }

      keep = (pinned == "true" || hits >= 3 || age_days <= max_age_days)
      if (keep) {
        printf "%s", entry
      }

      entry = ""
      added = ""
      hits = 1
      pinned = "false"
    }

    /^<!-- added:/ {
      flush_entry()
      in_entry = 1
      added = $0
      sub(/^<!-- added:[[:space:]]*/, "", added)
      sub(/[[:space:]]*-->$/, "", added)
      entry = $0 "\n"
      next
    }

    {
      if (!in_entry) {
        print
        next
      }

      entry = entry $0 "\n"

      if ($0 ~ /^<!-- hits:/) {
        hits = $0
        sub(/^<!-- hits:[[:space:]]*/, "", hits)
        sub(/[[:space:]]*-->$/, "", hits)
      } else if ($0 ~ /^<!-- pinned:/) {
        pinned = $0
        sub(/^<!-- pinned:[[:space:]]*/, "", pinned)
        sub(/[[:space:]]*-->$/, "", pinned)
      }
    }

    END {
      flush_entry()
    }
  ' "$BELIEFS" > "$tmp_file"

  mv "$tmp_file" "$BELIEFS"
}
