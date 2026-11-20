# WF-013 spec — Agent-driven steps: AI Review + agent span (source: 01 §WF-13, lines 390-413)


**Sites:** S11 + S3 `/text-box`. Only these steps may spend tokens, and only on purpose.

**Happy path:** record Wikipedia search "Marie Curie" → article → demoqa Full Name → Submit. Insert
an **AI Review** after the article: prompt "classify the subject: person, place, organization,
event, other", enum output `article_category`, on failure use default `other`. Put
`{{article_category}}` into Full Name. Compile → answer → test 5× → each run types `person` → approve.

| # | Break it | Must happen | If wrong |
|---|---|---|---|
| 13.1 | Insert a delete/remove step right after the AI Review | Save refused (`destructive_step_cannot_directly_follow_ai_review`) | S0 |
| 13.2 | Blank prompt | Dropped with a plain notice, not silently | S2 |
| 13.3 | Workflow with 6 AI Review steps, Studio test | Stops at the 5-pause cap with a plain message | S1 |
| 13.4 | Every other step in the run | Zero LLM calls | S1 |
| 13.5 | Insert an **agent span** around a click that looks irreversible | The click waits for a person's confirmation; the agent picks numbered actions, never selectors | S0 |
| 13.6 | Put the agent span inside a loop; also mark the run as a dry run | Span fails closed | S0 |
| 13.7 | Check the package | Says `uses_agent: true` | S1 |

The real-agent pause/resume part (bad answers, re-ask limit, park timeout) is in WF-16.
**Failures go to:** EXEC-13; 13.1/13.5/13.6 → PROD-3.

---

