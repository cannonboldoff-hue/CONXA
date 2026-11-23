# WF-021 spec — Go / no-go gate (source: 01 §WF-21)

## WF-21 — Go / no-go gate (last)

Only what no earlier WF covers. Clean Windows VM as the customer; accounts: owner, member, fresh
customer. Stop at the first failure, fix, restart that phase.

1. **Backend health:** `/healthz` 200 ×50, fast; `/readyz` 200; started with
   `SKILL_AUTH_REQUIRED=true` and every required secret; no filesystem-DB fallback in the logs.
2. **Dashboard:** loads under 5 s with no console errors; every screen as owner and as member; a
   member can't see or fetch owner actions.
3. **Customer install (clean VM):** `.exe` installs; `~/.conxa` has the host exe + `conxa-app/`;
   sync pulls packs with matching SHA-256; WF-16 rows 16.1–16.2 pass on that machine.
4. **Billing (Cashfree sandbox):** test card upgrades through the webhook; a failing card leaves no
   partial upgrade; hitting a limit shows a plain upgrade message that clears after paying.
5. **Updates:** a newer app layer downloads and loads; `min_host` is respected; a bad update rolls
   back; promotion checks Ed25519 signatures.
6. **Security spot checks:** session files encrypted; no API keys in the built dashboard JS.

**Hard blockers — any one is NO-GO:** any S0 still open from WF-4…WF-20 · credentials in build output
· `/readyz` failing · filesystem-DB fallback active · cross-company leak · Tier A spending tokens ·
broken update rollback. Sign GO / NO-GO with date, tester and versions at the end of the run log.

---
