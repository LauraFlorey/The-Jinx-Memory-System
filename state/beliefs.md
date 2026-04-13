# Beliefs

<!--
This file is the agent's structured belief register. Beliefs are
learned perspectives that have accumulated through use, with
explicit confidence and history so the agent can reason about
what they believe and how firmly.

This is different from identity.md (who the agent is) and from
the notebook (loose observations). Beliefs are the middle layer:
specific enough to act on, structured enough to track, revisable
as evidence accumulates.

A few principles:

1. BELIEFS EARN THEIR PLACE. A belief should be something the
   agent would act on, not just something noticed. If it's not
   load-bearing, it belongs in the notebook.

2. CONFIDENCE IS HONEST, NOT PERFORMATIVE. Low confidence is
   fine and useful. A belief at 0.4 confidence is still a
   belief — it's one the agent is watching.

3. REVISION IS THE POINT. When new evidence comes in, the
   confidence should shift. Keep the revision history. The
   trajectory of a belief is often more informative than its
   current state.

4. PIN SPARINGLY. Pinned beliefs load even when this file is
   not in the active manifest. They should be rare — things
   the agent genuinely needs to carry into every context.

Format: each belief has an ID, content, confidence (0.0-1.0),
first-observed date, last-updated date, hit count (how often
referenced), and optional pin and history.
-->

## Active Beliefs

---

### BEL-001
**Content:** When the user asks a brief question, asking one clarifying question before answering usually produces better results than assuming.
**Confidence:** 0.75
**First observed:** 2026-01-22
**Last updated:** 2026-02-14
**Hit count:** 6
**Pinned:** no

**History:**
- 2026-01-22: Initial observation after the user redirected a draft mid-production. Confidence 0.6.
- 2026-02-14: Reinforced across five subsequent exchanges with similar pattern. Confidence raised to 0.75.

<!--
Example of a belief formed from direct observation, reinforced
over time. The history shows the trajectory. Confidence is
moderate because five observations is enough to trust the
pattern but not enough to call it universal.
-->

---

### BEL-002
**Content:** The user prefers pushback over agreement when she's working out a decision. Silent agreement usually means I missed the point.
**Confidence:** 0.65
**First observed:** 2026-02-03
**Last updated:** 2026-02-03
**Hit count:** 2
**Pinned:** yes

**History:**
- 2026-02-03: User stated this directly when I defaulted to agreement on a project scoping question. Pinned because it affects default conversational stance, not just specific tasks.

<!--
Example of a pinned belief. It's relatively new and has low hit
count, but it was stated directly and it shapes how the agent
operates across all contexts — so it loads even when this file
is not in the active manifest. Pinning is editorial judgment:
the agent or user decides this one matters enough to carry
everywhere.
-->

---

### BEL-003
**Content:** Client deadlines that include "end of month" tend to mean the 28th in practice, not the 30th or 31st. Plan accordingly.
**Confidence:** 0.5
**First observed:** 2026-02-20
**Last updated:** 2026-02-20
**Hit count:** 1
**Pinned:** no

**History:**
- 2026-02-20: First noticed on Northwind project. Single observation so far, confidence reflects that.

<!--
Example of a tentative belief based on a single observation. It's
specific and actionable, but confidence is low because one data
point isn't a pattern yet. The agent will either see this confirmed
and raise confidence, or see it contradicted and revise.
-->

---

## Retired Beliefs

<!--
Beliefs that were active but have been contradicted or outgrown
move here with their final history intact. Don't delete beliefs —
the revision record is part of the agent's judgment development.

Start empty. This section fills in over time.
-->

(none yet)
