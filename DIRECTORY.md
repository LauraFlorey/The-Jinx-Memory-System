# The Jinx Memory System — directory map

This file lists every file under the repository root (`.git/` omitted). The **complete tree** and **flat file list** are generated from the filesystem together so the two sections stay in sync.

**Regenerate:** from the repo root, run `find . -type f ! -path './.git/*' | sed 's|^\./||' | sort` for a flat list, or re-run the tree generator used to refresh this document.

## Top-level overview

| Path | Role |
|------|------|
| `docs/` | Release notes and public sanitization checklist |
| `manifests/` | Manifest definitions for runs and workflows |
| `memory/` | Tiered memory: hot, warm, cold (with archived content placeholders) |
| `memory-engine/` | Promotion/demotion/summarizer modules, plus `backups/`, `processed/`, and `reports/` outputs |
| `projects/` | Per-project notes and state |
| `prompts/` | System and summary prompt templates |
| `scripts/` | Shell helpers (council, belief pruning) |
| `state/` | Runtime/session state, beliefs, progress, manifest test outputs |
| `tests/` | Python tests and integration shell script |
| `tools/` | CLI-style Python utilities (fetch, ingest, search, YouTube) |
| Root | Config, Discord bot, schedulers, ecosystem config, docs |

## Complete tree

Entries at each level are sorted alphabetically (case-insensitive). Dotfiles (for example `.env.example`) sort with other root names.

```
.
├── .env.example
├── .gitignore
├── beliefs-tools.sh
├── config.yaml
├── context-tools.sh
├── CURSOR-CONTEXT.md
├── deployment-status.md
├── DIRECTORY.md
├── discord-bot.py
├── docs
│   └── PUBLIC_SANITIZATION.md
├── ecosystem.config.js
├── LICENSE
├── loop.sh
├── manifests
│   ├── council-research.md
│   ├── council-synthesis.md
│   ├── default.md
│   ├── jinx-brief.md
│   ├── jinx-full.md
│   └── morning-brief.md
├── memory
│   ├── cold
│   │   ├── archived
│   │   │   └── .gitkeep
│   │   ├── archives
│   │   │   └── chat-history
│   │   │       └── .gitkeep
│   │   ├── family
│   │   │   ├── documents
│   │   │   │   └── .gitkeep
│   │   │   ├── narratives
│   │   │   │   └── .gitkeep
│   │   │   └── people
│   │   │       └── .gitkeep
│   │   ├── personal
│   │   │   ├── journal
│   │   │   │   └── .gitkeep
│   │   │   └── milestones
│   │   │       └── .gitkeep
│   │   ├── README.md
│   │   └── reference
│   │       ├── technical
│   │       │   └── .gitkeep
│   │       └── youtube
│   │           └── .gitkeep
│   ├── hot
│   │   └── identity.md
│   └── warm
│       ├── active-projects.md
│       ├── current-priorities.md
│       ├── jinx-notebook.md
│       └── laura.md
├── memory-engine
│   ├── backups
│   │   ├── 2026-04-08
│   │   │   ├── active-projects.md
│   │   │   ├── beliefs.md
│   │   │   ├── current-priorities.md
│   │   │   ├── jinx-notebook.md
│   │   │   └── laura.md
│   │   └── 2026-04-12
│   │       ├── active-projects.md
│   │       ├── beliefs.md
│   │       ├── jinx-notebook.md
│   │       └── laura.md
│   ├── demote.py
│   ├── processed
│   │   ├── summary-2026-04-06-20260406-115306-mid-161401392784.md
│   │   ├── summary-2026-04-06-20260406-115306-mid-163435094328.md
│   │   ├── summary-2026-04-06-20260406-115306-mid-180448452629.md
│   │   ├── summary-2026-04-06-20260406-115306-mid-215041957331.md
│   │   ├── summary-2026-04-07-20260407-024636-mid-172011456373.md
│   │   ├── summary-2026-04-07-20260407-024636-mid-173340638299.md
│   │   ├── summary-2026-04-07-20260407-024636.md
│   │   ├── summary-2026-04-08-20260408-172355-mid-174233093548.md
│   │   ├── summary-2026-04-08-20260408-172355-mid-174449868179.md
│   │   ├── summary-2026-04-08-20260408-172355-mid-231547308806.md
│   │   ├── summary-2026-04-10-20260410-011056-mid-013545302096.md
│   │   ├── summary-2026-04-10-20260410-011056-mid-014157810490.md
│   │   ├── summary-2026-04-10-20260410-011056-mid-015717292059.md
│   │   ├── summary-2026-04-10-20260410-011056-mid-115528844556.md
│   │   ├── summary-2026-04-10-20260410-011056-mid-120104470712.md
│   │   ├── summary-2026-04-10-20260410-011056-mid-120600651909.md
│   │   ├── summary-2026-04-10-20260410-011056-mid-120932029971.md
│   │   ├── summary-2026-04-10-20260410-011056-mid-212529103918.md
│   │   ├── summary-2026-04-11-20260410-011056-mid-000506167275.md
│   │   └── summary-2026-04-11-20260410-011056-mid-001001288973.md
│   ├── promoter.py
│   ├── reports
│   │   ├── promotion-2026-04-08.md
│   │   └── promotion-2026-04-12.md
│   └── summarizer.py
├── projects
│   ├── closepilot
│   │   └── state.md
│   ├── consulting
│   │   └── state.md
│   ├── council
│   │   ├── research-output-format.md
│   │   ├── research-topics.md
│   │   └── synthesis-format.md
│   ├── morning-brief
│   │   ├── brief-config.md
│   │   └── sources.md
│   ├── smc
│   │   └── state.md
│   └── test
│       └── task.md
├── prompts
│   ├── summary.md
│   └── system.md
├── README.md
├── requirements.txt
├── run-project.sh
├── scheduler.sh
├── scripts
│   ├── prune-beliefs.sh
│   └── run-council.sh
├── start-discord.sh
├── state
│   ├── belief-smoke
│   │   ├── beliefs.md
│   │   ├── progress.md
│   │   └── session-summary.md
│   ├── beliefs.md
│   ├── demo
│   │   ├── beliefs.md
│   │   ├── progress.md
│   │   └── session-summary.md
│   ├── demo-legacycheck
│   │   ├── beliefs.md
│   │   ├── progress.md
│   │   └── session-summary.md
│   ├── jinx-brief
│   │   ├── beliefs.md
│   │   ├── progress.md
│   │   └── session-summary.md
│   ├── manifests
│   │   ├── manifests-default
│   │   │   ├── progress.md
│   │   │   └── session-summary.md
│   │   ├── manifests-multi-iteration-test-v2
│   │   │   ├── progress.md
│   │   │   └── session-summary.md
│   │   └── manifests-multi-iteration-test-v3
│   │       ├── progress.md
│   │       └── session-summary.md
│   ├── morning-brief
│   │   ├── beliefs.md
│   │   ├── progress.md
│   │   └── session-summary.md
│   ├── progress.md
│   ├── prune-smoke
│   │   ├── beliefs.md
│   │   └── beliefs.md.bak
│   └── session-summary.md
├── tests
│   ├── test-demote.py
│   ├── test-discord-bot.py
│   ├── test-fetch-content.py
│   ├── test-ingest-document.py
│   ├── test-integration.sh
│   ├── test-memory-engine.py
│   ├── test-promoter.py
│   ├── test-search-memory.py
│   ├── test-summarizer.py
│   └── test-youtube-transcript.py
└── tools
    ├── fetch-content.py
    ├── ingest-document.py
    ├── search-memory.py
    └── youtube-transcript.py
```

## All files (flat list)

Total: **126** files (paths relative to repository root).

- `.env.example`
- `.gitignore`
- `CURSOR-CONTEXT.md`
- `DIRECTORY.md`
- `LICENSE`
- `README.md`
- `beliefs-tools.sh`
- `config.yaml`
- `context-tools.sh`
- `deployment-status.md`
- `discord-bot.py`
- `docs/PUBLIC_SANITIZATION.md`
- `ecosystem.config.js`
- `loop.sh`
- `manifests/council-research.md`
- `manifests/council-synthesis.md`
- `manifests/default.md`
- `manifests/jinx-brief.md`
- `manifests/jinx-full.md`
- `manifests/morning-brief.md`
- `memory-engine/backups/2026-04-08/active-projects.md`
- `memory-engine/backups/2026-04-08/beliefs.md`
- `memory-engine/backups/2026-04-08/current-priorities.md`
- `memory-engine/backups/2026-04-08/jinx-notebook.md`
- `memory-engine/backups/2026-04-08/laura.md`
- `memory-engine/backups/2026-04-12/active-projects.md`
- `memory-engine/backups/2026-04-12/beliefs.md`
- `memory-engine/backups/2026-04-12/jinx-notebook.md`
- `memory-engine/backups/2026-04-12/laura.md`
- `memory-engine/demote.py`
- `memory-engine/processed/summary-2026-04-06-20260406-115306-mid-161401392784.md`
- `memory-engine/processed/summary-2026-04-06-20260406-115306-mid-163435094328.md`
- `memory-engine/processed/summary-2026-04-06-20260406-115306-mid-180448452629.md`
- `memory-engine/processed/summary-2026-04-06-20260406-115306-mid-215041957331.md`
- `memory-engine/processed/summary-2026-04-07-20260407-024636-mid-172011456373.md`
- `memory-engine/processed/summary-2026-04-07-20260407-024636-mid-173340638299.md`
- `memory-engine/processed/summary-2026-04-07-20260407-024636.md`
- `memory-engine/processed/summary-2026-04-08-20260408-172355-mid-174233093548.md`
- `memory-engine/processed/summary-2026-04-08-20260408-172355-mid-174449868179.md`
- `memory-engine/processed/summary-2026-04-08-20260408-172355-mid-231547308806.md`
- `memory-engine/processed/summary-2026-04-10-20260410-011056-mid-013545302096.md`
- `memory-engine/processed/summary-2026-04-10-20260410-011056-mid-014157810490.md`
- `memory-engine/processed/summary-2026-04-10-20260410-011056-mid-015717292059.md`
- `memory-engine/processed/summary-2026-04-10-20260410-011056-mid-115528844556.md`
- `memory-engine/processed/summary-2026-04-10-20260410-011056-mid-120104470712.md`
- `memory-engine/processed/summary-2026-04-10-20260410-011056-mid-120600651909.md`
- `memory-engine/processed/summary-2026-04-10-20260410-011056-mid-120932029971.md`
- `memory-engine/processed/summary-2026-04-10-20260410-011056-mid-212529103918.md`
- `memory-engine/processed/summary-2026-04-11-20260410-011056-mid-000506167275.md`
- `memory-engine/processed/summary-2026-04-11-20260410-011056-mid-001001288973.md`
- `memory-engine/promoter.py`
- `memory-engine/reports/promotion-2026-04-08.md`
- `memory-engine/reports/promotion-2026-04-12.md`
- `memory-engine/summarizer.py`
- `memory/cold/README.md`
- `memory/cold/archived/.gitkeep`
- `memory/cold/archives/chat-history/.gitkeep`
- `memory/cold/family/documents/.gitkeep`
- `memory/cold/family/narratives/.gitkeep`
- `memory/cold/family/people/.gitkeep`
- `memory/cold/personal/journal/.gitkeep`
- `memory/cold/personal/milestones/.gitkeep`
- `memory/cold/reference/technical/.gitkeep`
- `memory/cold/reference/youtube/.gitkeep`
- `memory/hot/identity.md`
- `memory/warm/active-projects.md`
- `memory/warm/current-priorities.md`
- `memory/warm/jinx-notebook.md`
- `memory/warm/laura.md`
- `projects/closepilot/state.md`
- `projects/consulting/state.md`
- `projects/council/research-output-format.md`
- `projects/council/research-topics.md`
- `projects/council/synthesis-format.md`
- `projects/morning-brief/brief-config.md`
- `projects/morning-brief/sources.md`
- `projects/smc/state.md`
- `projects/test/task.md`
- `prompts/summary.md`
- `prompts/system.md`
- `requirements.txt`
- `run-project.sh`
- `scheduler.sh`
- `scripts/prune-beliefs.sh`
- `scripts/run-council.sh`
- `start-discord.sh`
- `state/belief-smoke/beliefs.md`
- `state/belief-smoke/progress.md`
- `state/belief-smoke/session-summary.md`
- `state/beliefs.md`
- `state/demo-legacycheck/beliefs.md`
- `state/demo-legacycheck/progress.md`
- `state/demo-legacycheck/session-summary.md`
- `state/demo/beliefs.md`
- `state/demo/progress.md`
- `state/demo/session-summary.md`
- `state/jinx-brief/beliefs.md`
- `state/jinx-brief/progress.md`
- `state/jinx-brief/session-summary.md`
- `state/manifests/manifests-default/progress.md`
- `state/manifests/manifests-default/session-summary.md`
- `state/manifests/manifests-multi-iteration-test-v2/progress.md`
- `state/manifests/manifests-multi-iteration-test-v2/session-summary.md`
- `state/manifests/manifests-multi-iteration-test-v3/progress.md`
- `state/manifests/manifests-multi-iteration-test-v3/session-summary.md`
- `state/morning-brief/beliefs.md`
- `state/morning-brief/progress.md`
- `state/morning-brief/session-summary.md`
- `state/progress.md`
- `state/prune-smoke/beliefs.md`
- `state/prune-smoke/beliefs.md.bak`
- `state/session-summary.md`
- `tests/test-demote.py`
- `tests/test-discord-bot.py`
- `tests/test-fetch-content.py`
- `tests/test-ingest-document.py`
- `tests/test-integration.sh`
- `tests/test-memory-engine.py`
- `tests/test-promoter.py`
- `tests/test-search-memory.py`
- `tests/test-summarizer.py`
- `tests/test-youtube-transcript.py`
- `tools/fetch-content.py`
- `tools/ingest-document.py`
- `tools/search-memory.py`
- `tools/youtube-transcript.py`
## Notes

- **`.git/`** is omitted; it holds Git metadata.
- **`.gitkeep`** entries mark intentionally empty directories preserved in version control.
