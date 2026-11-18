"use strict";

// Robot checks (reCAPTCHA, hCaptcha, Cloudflare Turnstile) are a wall a run must never try to get past by itself
// (WF-8 8.13). Mirrors conxa_compile/compiler/human_check.py, which leaves them out at compile and offers a hand-over.
// A run stops, with no recovery and no AI, when a step would act inside one or when a challenge is showing; a hand-over
// step is where a person does it, so it is never stopped.

// The frames a robot check lives in (only frames: reCAPTCHA's own demo page is an ordinary page).
const HUMAN_CHECK_FRAME = /^https?:\/\/(?:[\w-]+\.)*(?:google\.com|recaptcha\.net)\/recaptcha\/(?:api2|enterprise)\/(?:anchor|bframe)|^https?:\/\/(?:[\w-]+\.)*hcaptcha\.com\/|^https?:\/\/challenges\.cloudflare\.com\//i;
// A challenge put up in front of the page (the picture grid). Only these block a step that is not itself inside a
// robot check: the "I'm not a robot" box or a Turnstile widget beside a form does not stop typing into the form.
const CHALLENGE_FRAME = /^https?:\/\/(?:[\w-]+\.)*(?:google\.com|recaptcha\.net)\/recaptcha\/(?:api2|enterprise)\/bframe|^https?:\/\/(?:[\w-]+\.)*hcaptcha\.com\/.*frame=challenge/i;

const HUMAN_CHECK_MESSAGE = "This page is asking for a robot check (like “I’m not a robot”). A robot check can’t be done "
  + "automatically, so the run stopped here. Add a hand-over step so a person does it, then run again.";

function stepInHumanCheck(step) {
  const chain = step && step.frame && Array.isArray(step.frame.chain) ? step.frame.chain : [];
  return chain.some((c) => c && HUMAN_CHECK_FRAME.test(String(c.url || "")));
}

// "step" when the step itself acts inside a robot check, "challenge" when a challenge is showing, else null.
// Never throws: a page it cannot read is not a robot check.
async function humanCheckBlocking(page, step) {
  if (!step || step.type === "handover") return null;
  if (stepInHumanCheck(step)) return "step";
  const frames = page && typeof page.frames === "function" ? page.frames() : [];
  for (const f of frames) {
    let url = "";
    try { url = f.url(); } catch (_) { continue; }
    if (!CHALLENGE_FRAME.test(url)) continue;
    try {
      const el = await f.frameElement();
      if (el && await el.isVisible()) return "challenge";
    } catch (_) { /* detached or unreadable: not shown */ }
  }
  return null;
}

module.exports = { HUMAN_CHECK_FRAME, HUMAN_CHECK_MESSAGE, humanCheckBlocking, stepInHumanCheck };
