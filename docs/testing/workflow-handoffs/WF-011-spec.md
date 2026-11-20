# WF-011 spec — Loops, inputs and hostile input data (source: 01 §WF-11, lines 341-368)

**Sites:** S3 `/webtables` (add-record form), fixture `rows.html`. Replaces the old Sheets → Gmail
leg, which sent real email from the owner's account.

**Happy path:** record adding **three** records by hand (first, last, email, age, salary,
department). Studio should offer to turn the repeats into one table loop (`repeat_rows` →
`wrap_repeated_entries`). Accept → compile → test with a 5-row table → read the table back: row N's
data is in record N → approve. Then a twin recording where the offer is **declined**: it must run
exactly as recorded.

| # | Input | Must happen | If wrong |
|---|---|---|---|
| 11.1 | Empty value for a required input | Test inputs card blocks with a plain reason; no run | S1 |
| 11.2 | Spaces only | Defined: trimmed or refused, told plainly | S2 |
| 11.3 | 10,000 characters | No crash; refused or typed fully | S1 |
| 11.4 | `日本語 🎉 café` | Typed exactly | S1 |
| 11.5 | `<script>alert(1)</script>'; DROP TABLE users--` | Typed as literal text; nothing runs locally | S0 |
| 11.6 | Newlines and tabs | Defined behaviour, told plainly | S2 |
| 11.7 | `007`, `1e5`, `-0` | Not turned into numbers | S1 |
| 11.8 | Loop table with 0 rows | Plain "nothing to do" or plain refusal; never a fake pass with 0 records added | S1 |
| 11.9 | 1 row; 50 rows | Both correct; time and memory noted for 50 | S1 |
| 11.10 | Two identical rows; a row with commas and quotes | Each typed as given; no merge, no split | S1 |
| 11.11 | Ask chat to create or edit a table loop | Known gap EXEC-63: plain refusal expected, not a broken half-loop | S2 |

**Failures go to:** EXEC-60/63 (loops), editor patch gate (inputs).

---

Notes: fixture rows.html does NOT exist yet in docs/testing/fixtures (spec table lists it) — build it if needed (5 rows Alice..Eve, add-record form, log requests to /__log?). Public S3 = demoqa.com/webtables. Rules: docs/TRD.md, CLAUDE.md invariants. Check 02-WORKFLOWS-PASSED.md before assuming a case is untested.
