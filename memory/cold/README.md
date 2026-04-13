# Cold Storage

Cold storage is the archive tier for the agent's memory system. Files here are never auto-loaded into session context. They stay searchable on demand and can be promoted back into warm memory when they become relevant again.

## What Goes Here

- `archives/chat-history/` - imported or archived chat/session history
- `archived/` - stale warm-memory files moved here by demotion
- `family/documents/` - OCR output, scans, PDFs, letters, records
- `family/people/` - person-specific notes and profiles
- `family/narratives/` - longer family stories, timelines, synthesized narratives
- `personal/journal/` - journal-style reflections and entries
- `personal/milestones/` - life events, dates, and milestone notes
- `reference/` - saved web content and general reference material
- `reference/technical/` - technical articles, docs, engineering references
- `reference/youtube/` - saved YouTube transcripts and summaries

## Search

Search cold memory by keyword:

```sh
python3 tools/search-memory.py "keywords"
```

Search both cold and warm memory:

```sh
python3 tools/search-memory.py "keywords" --all
```

## How Files Get Here

- Warm-memory demotion via `python3 memory-engine/demote.py`
- Document ingestion and OCR via `python3 tools/ingest-document.py`
- Web/article capture via `python3 tools/fetch-content.py`
- YouTube transcript capture via `python3 tools/youtube-transcript.py`
- Imported or archived chat history

## Promote Back To Warm

Restore a demoted file back into warm memory:

```sh
python3 memory-engine/demote.py --restore memory/cold/archived/<file>.md
```
