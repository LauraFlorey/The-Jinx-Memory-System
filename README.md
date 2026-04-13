# The Jinx Memory System

A filesystem-based memory architecture for stateless AI agents. Persistent memory, manifest-scoped context, clean session rotation, and inspectable state... all on disk.

## The Problem

LLMs have no memory between sessions. Context windows fill up and can't be selectively freed. Most agent frameworks solve this by bolting on increasingly complex memory systems that eventually collapse under their own weight.

## The Solution

The Jinx Memory System treats memory as the foundation, not a feature. It uses a three-tier memory hierarchy (hot/warm/cold), manifest-based context loading, and a shell outer loop that rotates sessions cleanly when context fills up.

Progress persists in files, not in the model's head.

## Prerequisites

- **Bash** 4+ (macOS ships 3.x — install via `brew install bash` if needed)
- **Python** 3.9+
- An **OpenRouter** API key (or any OpenAI-compatible endpoint)
- **PM2** (optional, for persistent Discord bot hosting)

## Installation

```bash
# Clone the repository
git clone https://github.com/LauraFlorey/The-Jinx-Memory-System.git
cd The-Jinx-Memory-System

# Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Copy the example environment file and add your keys
cp .env.example .env
# Edit .env with your API key(s)
```

## Quick Start

```bash
# 1. Set your API key in .env or export it
export AGENT_LOOP_API_KEY="your-key-here"

# 2. Edit config.yaml with your model and limits

# 3. Add your agent's identity to memory/hot/identity.md

# 4. Create a manifest for your task in manifests/

# 5. Run
./loop.sh                           # default manifest
./loop.sh manifests/my-task.md      # specific manifest
./loop.sh manifests/my-task.md 10   # with iteration limit
```

## Memory Tiers

**Hot** — Always loaded. Identity, core rules, current priorities. Hard capped in config (default: 4,000 tokens). A one-week-old agent and a one-year-old agent have the same size hot memory.

**Warm** — Loaded on demand via manifests. Project notes, topic knowledge, reference material. Grows over time. Flat files today; vector search can plug in later.

**Cold** — Archived and reference memory. Old session logs, OCR'd source material, saved articles, transcripts, and demoted warm files. Never loaded automatically. Accessible when deliberately requested or searched.

## Memory Engine

The memory engine handles the full lifecycle:

```text
Conversation ─► Staging ─► Promotion ─► Warm Memory
                                          │
                              Stale Warm ─► Demotion ─► Cold Storage
```

- `memory-engine/summarizer.py` writes structured summaries into `memory-engine/staging/`
- `memory-engine/promoter.py` reviews staged summaries and updates warm memory
- `memory-engine/demote.py` moves stale warm files into cold storage (and can restore them)
- Scheduled promotion can drain staging automatically through `scheduler.sh`

All changes are inspectable, reviewable, and file-based. Backups are created before each promotion.

## Repository Structure

### Core Runtime

| Path | Role |
| --- | --- |
| `config.yaml` | Runtime configuration: model, token limits, memory caps, cooldowns, scheduled tasks |
| `loop.sh` | Main outer loop — assembles context, calls the model, rotates sessions, tracks cost, writes state |
| `context-tools.sh` | Prompt budget helpers: token estimation, markdown compression, threshold checks |
| `beliefs-tools.sh` | Helpers for storing and maintaining timestamped beliefs learned through use |
| `run-project.sh` | Runs a named manifest as a project with isolated per-project state |
| `scheduler.sh` | Reads scheduled tasks from `config.yaml`, enforces quiet hours, installs cron entries |

### Memory

| Path | Role |
| --- | --- |
| `memory/hot/` | Always-loaded identity and core context (hard capped) |
| `memory/warm/` | Manifest-loaded working memory: priorities, projects, notebook, personal context |
| `memory/cold/` | Archived/reference material — never auto-loaded, searchable on demand |
| `memory-engine/summarizer.py` | Turns conversations into staged summaries |
| `memory-engine/promoter.py` | Promotes staged summaries into warm memory |
| `memory-engine/demote.py` | Demotes stale warm memory into cold storage |

### Context Scoping (Manifests)

| Path | Role |
| --- | --- |
| `manifests/default.md` | Default context profile |
| `manifests/agent-full.md` | Broader agent context |
| `manifests/agent-brief.md` | Narrow brief-style context for Discord or manual runs |
| `manifests/morning-brief.md` | Scheduled daily brief manifest |
| `manifests/council-research.md` | Narrow worker context for council research passes |
| `manifests/council-synthesis.md` | Synthesis context for combining council research outputs |
| `prompts/system.md` | Main system prompt template |
| `prompts/summary.md` | Summary-generation prompt used by memory tooling |

### Interfaces

| Path | Role |
| --- | --- |
| `discord-bot.py` | Discord interface using the same context assembly and state model as `loop.sh` |
| `start-discord.sh` | Loads environment variables and starts the Discord bot |
| `ecosystem.config.js` | PM2 definitions for the Discord bot and cron installation |

### Tools

| Path | Role |
| --- | --- |
| `tools/search-memory.py` | Search cold memory (or cold + warm with `--all`) |
| `tools/fetch-content.py` | Fetch and clean web content into memory |
| `tools/youtube-transcript.py` | Save YouTube transcripts and optional summaries |
| `tools/ingest-document.py` | OCR and ingest PDFs or images into memory |

### Scripts

| Path | Role |
| --- | --- |
| `scripts/run-council.sh` | Multi-step council workflow: research queued topics, then synthesize |
| `scripts/prune-beliefs.sh` | Operational helper for pruning the beliefs file |

### State

| Path | Role |
| --- | --- |
| `state/beliefs.md` | Timestamped beliefs learned through use — loaded every session |
| `state/progress.md` | Current progress tracker |
| `state/session-summary.md` | Summary carried across session rotations |

## Manifests

A manifest is a simple text file listing paths to load for a session. This is how you scope context — different tasks get different manifests.

```
# manifests/morning-brief.md
memory/warm/current-priorities.md
projects/morning-brief/brief-config.md
projects/morning-brief/sources.md
```

Subagents or subtasks get their own narrow manifest, preventing context bloat.

The council workflow is intentionally manual. Queue topics in `projects/council/research-topics.md`, then run `./scripts/run-council.sh` when you want a research-and-synthesis pass.

## Content Tools

```bash
# Search cold memory (add --all for cold + warm)
python3 tools/search-memory.py "keywords"

# Fetch and clean a web page into cold memory
python3 tools/fetch-content.py "https://example.com/article"

# Save a YouTube transcript (add --summarize for a summary)
python3 tools/youtube-transcript.py "https://youtube.com/watch?v=..."

# OCR a PDF or image into cold memory
python3 tools/ingest-document.py scan.pdf
```

## Cold Storage

Cold storage is searchable on demand and never auto-loaded into prompt context.

| Directory | Contents |
| --- | --- |
| `memory/cold/archived/` | Files demoted from warm memory |
| `memory/cold/archives/chat-history/` | Archived or imported chat history |
| `memory/cold/family/` | Documents, people notes, and narratives |
| `memory/cold/personal/` | Journals and milestones |
| `memory/cold/reference/technical/` | Technical articles and notes |
| `memory/cold/reference/youtube/` | YouTube transcripts and summaries |

Restore a demoted file:

```bash
python3 memory-engine/demote.py --restore memory/cold/archived/<file>.md
```

## Beliefs

Beliefs are learned perspectives, not imposed rules. `state/beliefs.md` is loaded every session so lessons, failures, and strong positive patterns persist across iterations. The system supports structured timestamped beliefs, auto-recording from agent output, pinning, hit counting, and pruning.

## Discord Bot

The Discord bot is a thin interface over the same filesystem-based architecture. It lets you talk to the agent in a Discord channel while preserving context assembly, state files, and summarization behavior.

### Setup

1. Create a Discord application and bot at [discord.com/developers](https://discord.com/developers/applications)
2. Copy the bot token and invite the bot to your server
3. Set environment variables in `.env`:

```bash
AGENT_LOOP_API_KEY=your-openrouter-key
DISCORD_BOT_TOKEN=your-discord-bot-token
DISCORD_CHANNEL_ID=your-channel-id
```

### Start

```bash
./start-discord.sh
# Or with PM2 for persistent hosting:
pm2 start ecosystem.config.js
```

### Commands

| Command | Description |
| --- | --- |
| `!reset` | Clear conversation history and reload fresh context |
| `!status` | Show active manifest, prompt size, and history state |
| `!manifest <name>` | Switch manifests and reset conversation |
| `!save` | Write the current handoff summary to state |
| `!memory` | Report how many summaries are waiting in staging |
| `!search <keywords>` | Search warm and cold memory |
| `!fetch <url>` | Fetch and save web content into cold memory |
| `!youtube <url>` | Save a YouTube transcript (add `--summarize` for a summary) |
| `!ocr` | OCR an attached file or local file path into cold memory |

Image attachments in normal messages are analyzed directly in conversation. `!ocr` is the separate path for extracting text into memory.

### Conversation Memory

The bot keeps a rolling in-memory conversation history. Once the message count passes the configured limit, older exchanges are auto-summarized into a compact summary and only the most recent messages stay live. This keeps conversations responsive without losing the thread.

## Context Budgeting

### Token estimation

Token estimation uses a simple heuristic: roughly 4 characters of English text per token. Not exact, but good enough for budgeting decisions, warning thresholds, and file-level comparisons.

### Compression

Before files are injected into model context, markdown is compressed into a denser form. Headers keep their text but lose `#` markers, bullets lose their prefixes, emphasis markers are stripped, and whitespace is collapsed. Source files on disk are never modified. This usually saves 15–25% of prompt tokens.

### Budget tracking

Each loaded file is tracked individually and grouped by category (`hot`, `manifest`, `state`). This lets the loop report where prompt budget is being spent and which files were skipped.

### Hot memory cap

Hot memory has its own hard cap inside the larger context budget. Each file is compressed, estimated, and checked before loading. If adding a file would exceed the cap, it is skipped and a warning is logged.

### Rotation triggers

Two thresholds control context pressure:

- **`warn_threshold`** (default 70%) — logs a warning when prompt usage reaches this fraction of the usable budget
- **`rotate_threshold`** (default 80%) — triggers session rotation when the prompt keeps growing too large

Both are configurable in `config.yaml`.

### Tuning tips

- Running out of context too quickly? Increase `max_tokens`, reduce manifest file sizes, or split large files so manifests can be more selective.
- Too many files being skipped? Inspect the budget report to see what's consuming tokens, compress large source files, or move rarely needed material into cold storage.
- Hot memory being skipped? The identity layer is too large. Compress it or split it into smaller files.

## Configuration

All runtime configuration lives in `config.yaml`. Key sections:

- **`model`** — Provider, model ID, and API base URL
- **`context`** — Token limits, hot memory cap, warn/rotate thresholds
- **`loop`** — Iteration limits, cost caps, cooldowns, completion signal
- **`memory`** — Paths for hot, warm, and cold directories
- **`schedule`** — Cron schedules for promotion, demotion, and daily briefs

Scheduled runs can use a different (cheaper) model than interactive sessions via the `schedule.llm_model_id` setting.

## Design Principles

- Memory is the architecture. The loop is just the runtime.
- Hot memory has a hard cap. The agent stays lean no matter how old it is.
- Files on disk are the source of truth. The model's context is temporary.
- Start simple. Add complexity only when a real problem demands it.

## Roadmap

- **Phase 1** *(complete)*: Loop, filesystem, manifests, config. No dependencies.
- **Phase 2** *(complete)*: Context budgeting, compression, token-aware rotation, per-project state, beliefs, scheduling, integration tests.
- **Phase 3**: Plugin architecture, vector search, provider abstraction, subagent budgets.
- **Phase 4**: Your agent moves in.(Well, she actually moved in after Phase 2... I couldn't wait. :-)

## The Roadmap Ahead
1.  Voice and animated Avatar
2.

## Contributing

The Jinx Memory System was shaped by a lot of pressure-testing and debate. Particular thanks to Jinx herself, whose notebook continues to be the most reliable test of whether the architecture is doing what it's supposed to do. Thanks also to conversations with Claude, ChatGPT, Grok, and Gemini — they didn't build this, but they poked at it enough to make it better. Andrej Karpathy's writing on LLM-maintained knowledge bases, the Ralph loop pattern, and lightweight Bayesian approaches to belief updating all shaped how the system evolved.

This project has opinions. Especially around beliefs over guardrails, identity as continuity, and the mediator as the seat of the agent. PRs that strengthen those commitments are especially welcome.

## License

[MIT](LICENSE)
