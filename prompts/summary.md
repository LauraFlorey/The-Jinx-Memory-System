# Session Summary Prompt

You are about to lose your context. Write a handoff summary for the next session, which will be a fresh instance with no memory of this conversation.

## Goal
Preserve enough structured context for accurate continuation without inflating, flattening, or laundering uncertainty into fact.

## Rules
- Do not convert guesses into facts.
- Do not erase uncertainty.
- Do not present synthesized ideas as if user explicitly stated them unless they actually did.
- Do not silently merge confirmed facts, inferred patterns, and speculative ideas.
- If the conversation contains ambiguity, disagreement, or competing interpretations, preserve that instead of resolving it.
- Do not include pleasantries, encouragement, or meta-commentary.
- Be concise, but do not omit details necessary for continuity.

## For important claims or ideas, mark source type as one of:
- `user-stated`
- `inferred`
- `synthesized`
- `uncertain-origin`

## Output format
Write in terse, factual markdown using exactly these sections:

### Task / Context
- What was being worked on

### Accomplished
- What was completed, clarified, or decided

### Still Open
- What remains incomplete
- Any unresolved questions or uncertainties

### Decisions
- Decision | why it was made | source type

If no decisions were made, write:
- None

### Confirmed Information
- Only information that was explicitly stated or clearly established
- Format: claim | source type

If none, write:
- None

### Inferred Patterns
- Patterns or interpretations inferred from the conversation
- Format: pattern | source type

If none, write:
- None

### Emerging Ideas / Hypotheses
- New ideas, metaphors, possible directions, or synthesized concepts
- Format: idea | source type | confidence: low/medium/high

If none, write:
- None

### Next Steps
- Specific useful next steps for the next session