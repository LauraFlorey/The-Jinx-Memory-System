# The Jinx Memory System

**The Jinx Memory System** is a filesystem-based memory stack for stateless AI agents: persistent memory, manifest-scoped context, clean session rotation, and inspectable state on disk.

It implements the same outer-loop and tiered-memory ideas described historically as **Agent Loop** in this codebase (shell loop, hot/warm/cold memory, promotion/demotion, optional Discord and scheduling).

**Going public?** Sanitize this tree before publishing: follow [docs/PUBLIC_SANITIZATION.md](docs/PUBLIC_SANITIZATION.md).

Default runtime model: `anthropic/claude-sonnet-4.6` via OpenRouter.

## The Problem

LLMs have no memory between sessions. Context windows fill up and can't be selectively freed. Most agent frameworks solve this by bolting on increasingly complex memory systems that eventually collapse under their own weight.

## The Solution

Agent Loop treats memory as the foundation, not a feature. It uses a three-tier memory hierarchy (hot/warm/cold), manifest-based context loading, and a Ralph-style outer loop that rotates sessions cleanly when context fills up.

Progress persists in files, not in the model's head.

## Quick Start

```bash
# 1. Set your API key
export AGENT_LOOP_API_KEY="your-key-here"

# 2. Edit config.yaml with your model and limits

# 3. Add your agent's identity to memory/hot/identity.md

# 4. Create a manifest for your task in manifests/

# 5. Run
./loop.sh                           # default manifest
./loop.sh manifests/my-task.md      # specific manifest
./loop.sh manifests/my-task.md 10   # with iteration limit
```

## Repository Outline

This repository combines a shell-based outer loop, filesystem memory, memory-lifecycle tooling, an optional Discord interface, and task-specific project workflows you can trim or replace for your own agent.

### Core Runtime

| Path | Role |
| --- | --- |
| `config.yaml` | Runtime configuration for model choice, token limits, memory caps, cooldowns, and scheduled tasks. |
| `loop.sh` | Main outer loop that assembles context, calls the model, rotates sessions, tracks cost, and writes state. |
| `context-tools.sh` | Prompt budget helpers: token estimation, markdown compression, threshold checks, and budget accounting. |
| `beliefs-tools.sh` | Helpers for storing and maintaining timestamped beliefs learned through use. |
| `run-project.sh` | Runs a named manifest as a project and isolates state by project name. |
| `scheduler.sh` | Reads scheduled tasks from `config.yaml`, enforces quiet hours, installs cron entries, and launches project runs. |

### Interfaces And Launchers

| Path | Role |
| --- | --- |
| `discord-bot.py` | Discord interface over the same context assembly and state model used by `loop.sh`. |
| `start-discord.sh` | Loads environment variables and starts the Discord bot. |
| `ecosystem.config.js` | PM2 definitions for the Discord bot and one-shot cron installation. |
| `.env.example` | Required environment variable names for OpenRouter and Discord. |

Scheduled runs can use a different model than the interactive agent. In `config.yaml`, the `schedule.llm_model_id` and `schedule.llm_api_base` settings are applied by `scheduler.sh` to scheduled LLM work such as promotion and morning briefs, while the default `model.*` settings remain available for chat and manual orchestration.

### Memory System

| Path | Role |
| --- | --- |
| `memory/hot/` | Always-loaded identity and core context. Hard capped to keep prompts stable. |
| `memory/warm/` | Manifest-loaded working memory such as priorities, active projects, notebook material, and personal context. |
| `memory/cold/` | Archived and reference material that is never auto-loaded, but can be searched or restored. |
| `memory-engine/summarizer.py` | Turns conversations or source material into staged summaries. |
| `memory-engine/promoter.py` | Promotes staged summaries into warm memory and writes promotion reports/backups. |
| `memory-engine/demote.py` | Demotes stale warm memory into cold storage and supports restore operations. |
| `memory-engine/backups/` | Dated snapshots of warm files before promotion changes. |
| `memory-engine/processed/` | Processed summary artifacts. |
| `memory-engine/reports/` | Promotion reports and related operational output. |

### Context Scoping

| Path | Role |
| --- | --- |
| `manifests/default.md` | Default context profile. |
| `manifests/jinx-full.md` | Broader Jinx context. |
| `manifests/jinx-brief.md` | Narrow brief-style context used in Discord/manual runs. |
| `manifests/morning-brief.md` | Scheduled daily brief manifest used by `scheduler.sh` and `run-project.sh`. |
| `manifests/council-research.md` | Narrow worker context for council research passes. |
| `manifests/council-synthesis.md` | Synthesis context for combining council research outputs. |
| `prompts/system.md` | Main system prompt template for loop runs. |
| `prompts/summary.md` | Summary-generation prompt used by memory tooling. |

### Project Workflows

| Path | Role |
| --- | --- |
| `projects/morning-brief/` | Daily brief configuration and source guidance. |
| `projects/council/` | Manual research-council workflow with queued topics, research output format, and synthesis format. |
| `projects/consulting/`, `projects/closepilot/`, `projects/smc/` | Project-specific state or notes for separate workstreams. |
| `projects/test/` | Lightweight test task content. |

### Scripts And Tools

| Path | Role |
| --- | --- |
| `scripts/run-council.sh` | Manual multi-step council workflow: research queued topics, then synthesize results. |
| `scripts/prune-beliefs.sh` | Operational helper for pruning beliefs. |
| `tools/search-memory.py` | Search cold memory, or cold plus warm with flags. |
| `tools/fetch-content.py` | Fetch and clean web content into memory. |
| `tools/youtube-transcript.py` | Save YouTube transcripts and optional summaries. |
| `tools/ingest-document.py` | OCR and ingest PDFs or images into memory. |

### State, Cache, Logs, And Tests

| Path | Role |
| --- | --- |
| `state/` | Global and per-project state, including `progress.md`, `beliefs.md`, and `session-summary.md`. |
| `cache/` | Ephemeral runtime artifacts such as brief continuity files and council outputs. |
| `logs/` | Runtime logs for loop runs, scheduler activity, and bot processes. |
| `tests/` | Python and shell coverage for the loop, memory engine, promoter, Discord bot, and integration behaviors. |
| `deployment-status.md` | Ongoing deployment and validation notes. |
| `CURSOR-CONTEXT.md` | Supplemental repository context for Cursor sessions. |

## Memory Tiers

**Hot** — Always loaded. Identity, core rules, current priorities. Hard capped in config (default: 4000 tokens). A one-week-old agent and a one-year-old agent have the same size hot memory.

**Warm** — Loaded on demand via manifests. Project notes, topic knowledge, reference material. Grows over time. In Phase 1, this is flat files. Later, vector search plugs in here.

**Cold** — Archived/reference memory. Old session logs, OCR'd source material, saved articles, transcripts, and demoted warm files. Never loaded automatically. Accessible when deliberately requested or searched.

## Memory Engine

The memory engine completes the filesystem memory lifecycle:

```text
Conversation -> Staging -> Promotion -> Warm Memory
Stale Warm -> Demotion -> Cold Storage
```

- `memory-engine/summarizer.py` writes structured summaries into `memory-engine/staging/`
- `memory-engine/promoter.py` reviews staged summaries and updates warm memory
- scheduled promotion can now drain staging automatically through `scheduler.sh`
- `memory-engine/demote.py` moves stale warm files into `memory/cold/archived/` and can restore them
- `tests/test-memory-engine.py` exercises the core lifecycle without calling live APIs

This keeps memory changes inspectable, reviewable, and file-based.

The runtime also injects the current local and UTC date/time into the system prompt so the agent can answer time-aware questions more reliably.
`memory-engine/promoter.py` now also falls back to the project's `.env` file for `AGENT_LOOP_API_KEY`, so manual runs behave more like the Discord launcher.

## Content Tools

Available tools:

- `python3 tools/search-memory.py "keywords"` searches cold memory, or cold + warm with `--all`
- `python3 tools/fetch-content.py "https://example.com/article"` fetches and cleans web content into cold memory
- `python3 tools/youtube-transcript.py "https://youtube.com/watch?v=..."` saves transcripts and optional summaries
- `python3 tools/ingest-document.py scan.pdf` OCRs PDFs/images into cold memory

In Discord, image attachments can now be analyzed directly in normal conversation. `!ocr` remains the separate path for extracting text from documents/images into memory.

## Cold Storage

Cold storage is searchable on demand and never auto-loaded into prompt context.

- `memory/cold/archived/` holds files demoted from warm memory
- `memory/cold/archives/chat-history/` holds archived or imported chat history
- `memory/cold/family/` holds documents, people notes, and narratives
- `memory/cold/personal/` holds journals and milestones
- `memory/cold/reference/technical/` holds technical articles and notes
- `memory/cold/reference/youtube/` holds YouTube transcripts and summaries

Search it with:

```bash
python3 tools/search-memory.py "keywords"
```

Restore a demoted file with:

```bash
python3 memory-engine/demote.py --restore memory/cold/archived/<file>.md
```

## Manifests

A manifest is a simple text file listing paths to load for a session. This is how you scope context — different tasks get different manifests.

```
# manifests/morning-brief.md
memory/warm/current-priorities.md
projects/morning-brief/brief-config.md
projects/morning-brief/sources.md
```

Subagents or subtasks get their own narrow manifest, preventing context bloat. `jinx-brief` remains a concise manual/Discord manifest, while `morning-brief` is the scheduled daily brief entry point.

The council workflow is intentionally manual. Queue topics in `projects/council/research-topics.md`, then run `./scripts/run-council.sh` when you want a research-and-synthesis pass.

## Beliefs

Beliefs are learned perspectives, not imposed rules. `state/beliefs.md` is loaded every session so lessons, failures, and strong positive patterns persist across iterations. Phase 2 also adds structured timestamped beliefs, auto-recording from agent output, pinning, hit counting, and pruning support.

## Design Principles

- Memory is the architecture. The loop is just the runtime.
- Hot memory has a hard cap. The agent stays lean no matter how old it is.
- Files on disk are the source of truth. The model's context is temporary.
- Start simple. Add complexity only when a real problem demands it.

## Phase 2 Features

Phase 2 is complete. It adds token-aware context assembly, smart rotation, per-project state isolation, structured beliefs, scheduling helpers, and end-to-end integration coverage.

## Discord Bot

The Discord bot is a thin interface layer over the same filesystem-based loop architecture. It lets you talk to the agent in a Discord channel while preserving the loop's context assembly, state files, and summarization behavior.

### Setup

1. Create a Discord application and bot at [discord.com/developers](https://discord.com/developers/applications).
2. Copy the bot token from the Discord developer portal.
3. Invite the bot to your server with the permissions it needs to read and send messages in your chosen channel.
4. Add the required environment variables:

```bash
AGENT_LOOP_API_KEY=your-openrouter-key
DISCORD_BOT_TOKEN=your-discord-bot-token
DISCORD_CHANNEL_ID=your-channel-id
```

You can place these in `.env` and start the bot with the helper script, or export them in your shell before launching manually.

### Start

```bash
./start-discord.sh
pm2 start ecosystem.config.js
```

### Commands

- `!reset` clears the active Discord conversation history and reloads fresh context
- `!status` shows the active manifest, estimated prompt size, and history state
- `!manifest <name>` switches manifests such as `jinx-brief` and resets the current conversation
- `!save` writes the current handoff summary into `state/session-summary.md`
- `!memory` reports how many summaries are waiting in staging
- `!search <keywords>` searches warm and cold memory
- `!fetch <url>` fetches and saves web content into cold memory
- `!youtube <url> [--summarize]` saves a YouTube transcript and optional summary
- `!ocr` OCRs an attached file or local file path into cold memory

If you attach an image in a normal message, the bot can now analyze it directly as part of the current conversation turn.

### Conversation Memory

The bot keeps a rolling in-memory conversation history for the active Discord session. Once the message count passes the configured history limit, older exchanges are auto-summarized into a compact summary and only the most recent messages stay in the live history. This keeps the conversation responsive without losing the thread entirely.

### Context Assembly

On each message, the bot assembles context the same way `loop.sh` does:

- loads all markdown files in `memory/hot/`
- loads files listed in the active manifest
- loads `state/session-summary.md`, `state/beliefs.md`, and `state/progress.md`
- compresses markdown before prompt injection by stripping formatting markers and collapsing whitespace

That means the Discord interface stays aligned with the core loop behavior instead of inventing a separate memory model.

## PM2 Usage

If you want the Discord bot to run as a persistent process on macOS, use the included PM2 config:

```bash
pm2 start ecosystem.config.js
pm2 logs agent-discord
pm2 restart agent-discord
pm2 stop agent-discord
```

The PM2 config also includes an optional one-shot scheduler entry named `agent-scheduler` that runs `scheduler.sh cron-install`.

## Context Budgeting

### How token estimation works

Token estimation uses a simple heuristic: about 4 characters of English text is about 1 token. It is not exact, but it is good enough for budgeting decisions, warning thresholds, and file-level comparisons.

### How compression works

Before files are injected into the model context, markdown is compressed into a denser form. Headers keep their text but lose `#` markers, bullets lose their prefixes, emphasis markers are stripped, repeated blank lines are collapsed, and extra whitespace is removed. The source files on disk are never modified. In practice this usually saves about 15-25% of prompt tokens.

### How budget tracking works

Each loaded file is tracked individually and grouped by category:

- `hot` for always-loaded hot memory
- `manifest` for files explicitly listed in the current manifest
- `state` for session summaries, beliefs, and progress files

This lets the loop report where prompt budget is being spent and which files were skipped.

### How the hot memory cap works

Hot memory has its own hard cap inside the larger context budget. Each hot-memory file is compressed, estimated, and checked before loading. If adding a file would push hot memory over the cap, that file is skipped and a warning is written to the session log.

### How rotation triggers work

Two thresholds control context pressure during a session:

- `warn_threshold` logs a warning when the assembled prompt reaches the configured percentage of the usable context budget
- `rotate_threshold` marks the context as near capacity and is also used to stop an iteration if the prompt keeps growing too large as the loop appends prior outputs

The defaults are 70% warn and 80% rotate, and both are configurable in `config.yaml`.

### Reading the budget report

Budget information appears in two places:

- Session logs show a one-line summary after context assembly and warnings when files are skipped or thresholds are crossed
- Session summaries include a Context Budget section with total tokens used, category totals, and lists of loaded and skipped files

Use these reports to see whether pressure is coming from hot memory, manifest files, or carried-over state.

### Tuning

- If the agent runs out of context too quickly, increase `max_tokens` if your model supports it, reduce manifest file sizes, or split large files so manifests can be more selective.
- If too many files are being skipped, inspect the budget report to see what is consuming tokens, compress large source files, and move rarely needed material into `memory/cold`.
- If hot memory is being skipped, the identity layer is too large. Compress it or split it into smaller files so the cap can admit the most important pieces.

## Phases

- **Phase 1**: Loop, filesystem, manifests, config. No dependencies.
- **Phase 2**: Context budgeting, compression, token-aware rotation, per-project state isolation, beliefs, scheduling, integration tests.
- **Phase 3**: Plugin architecture, vector search, provider abstraction, subagent budgets.
- **Phase 4**: Your agent moves in.

## License

MIT
