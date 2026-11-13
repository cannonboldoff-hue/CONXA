"use strict";
/**
 * Mechanical guard for EXEC-34/EXEC-29's "every page/locator .evaluate()/.count() call routes
 * through page_eval.js's deadline seam" rule (see CLAUDE.md/TODO.md EXEC-34, page_eval.js's own
 * header comment for why: Playwright gives evaluate()/count() no timeout of their own, so a
 * blocked renderer — a native dialog, a beforeunload prompt, a throttled background tab — can
 * hang the call past the run's own EXECUTION_DEADLINE_MS watchdog, which is poll-based and
 * cannot interrupt a call it isn't awaiting between operations).
 *
 * Scans every runtime/app/*.js file except page_eval.js itself (the seam) and page_scripts.js
 * (functions that run IN the page realm via evalOn/locator serialization — they call browser
 * DOM APIs, not Playwright's own .evaluate()/.count()) for a bare `.evaluate(`/`.count(` call.
 * A narrow allow-list covers identifier collisions that are not Playwright calls at all
 * (runRegistry.count(), policyGate.evaluate()).
 *
 * Second rule (the "a0l is not defined" class): the function an evalOn() runs in the page must come from page_scripts.js
 * (`pageScripts.name`). Playwright re-parses it standalone in the page, and every other app module is built with a
 * string-array decoder (build-runtime-app.yml / build-app-local.ps1) that does not exist there, so an inline or
 * module-local function throws ReferenceError in the built app layer while passing every test from source. The
 * caller's own catch then hides it and the probe silently reads as "nothing found" (or, for a check, "fine").
 * A string argument (a script source) is not a function and is allowed.
 *
 * Usage: node check_page_eval_seam.js   (exit 1 on violation)
 */
const fs = require("fs");
const path = require("path");

const root = __dirname;
const EXCLUDE_FILES = new Set(["page_eval.js", "page_scripts.js", "check_page_eval_seam.js"]);

// Identifier prefixes known NOT to be a Playwright Page/Locator/FrameLocator — checked against
// the text immediately before the matched `.evaluate(`/`.count(` call.
const ALLOWED_PREFIXES = [/\brunRegistry\.count\($/, /\bpolicyGate\.evaluate\($/];

function stripComments(src) {
  // Naive but sufficient for this codebase's style: strip //... line comments and /* ... */
  // block comments. Does not attempt to be string-literal-aware (no `//` or `/*` appears inside
  // a string literal anywhere near an .evaluate(/.count( call site in this codebase today).
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/([^:])\/\/.*$/gm, "$1");
}

function findViolations(file, src) {
  const clean = stripComments(src);
  const violations = [];
  const re = /[.\]]\s*(evaluate|count)\s*\(/g;
  let m;
  while ((m = re.exec(clean))) {
    const before = clean.slice(Math.max(0, m.index - 60), m.index + m[0].length);
    if (ALLOWED_PREFIXES.some((p) => p.test(before))) continue;
    const line = clean.slice(0, m.index).split("\n").length;
    violations.push(`${file}:${line}: bare .${m[1]}( call — route through page_eval.js's evalOn/countOn`);
  }
  return violations;
}

/** The text of evalOn's second argument at each call, or null when the call has fewer than two arguments. */
function evalOnFunctionArgs(clean) {
  const found = [];
  const re = /\bevalOn\(/g;
  let m;
  while ((m = re.exec(clean))) {
    let depth = 0;
    let i = m.index + m[0].length;
    let comma = -1;
    let quote = null;
    for (; i < clean.length; i++) {
      const ch = clean[i];
      if (quote) { if (ch === "\\") i++; else if (ch === quote) quote = null; continue; }
      if (ch === '"' || ch === "'" || ch === "`") { quote = ch; continue; }
      if ("([{".includes(ch)) depth++;
      else if (")]}".includes(ch)) { if (depth === 0) break; depth--; }
      else if (ch === "," && depth === 0) { comma = i; break; }
    }
    if (comma < 0) continue;
    found.push({ index: m.index, arg: clean.slice(comma + 1, comma + 80).trim() });
  }
  return found;
}

// An identifier is fine when this file bound it to page_scripts.js (`const script = ... pageScripts.x` or a destructured require).
function fromPageScripts(clean, id) {
  const bound = new RegExp(String.raw`(?:const|let)\s+${id}\s*=[^;]*pageScripts\.`);
  const imported = new RegExp(String.raw`\{[^}]*\b${id}\b[^}]*\}\s*=\s*(?:pageScripts|require\(["']\./page_scripts["']\))`);
  return bound.test(clean) || imported.test(clean);
}

function findInPageViolations(file, src) {
  const clean = stripComments(src);
  return evalOnFunctionArgs(clean)
    .filter(({ arg }) => {
      if (/^pageScripts\.\w+/.test(arg) || /^["'`]/.test(arg) || /^\w+\(/.test(arg)) return false; // a script string, or a call that builds one
      const id = /^(\w+)\s*[,)]/.exec(arg);
      return !(id && fromPageScripts(clean, id[1]));
    })
    .map(({ index, arg }) => {
      const line = clean.slice(0, index).split("\n").length;
      return `${file}:${line}: evalOn() runs "${arg.slice(0, 30)}" in the page — move it to page_scripts.js and pass pageScripts.<name>`;
    });
}

const files = fs.readdirSync(root).filter((f) => f.endsWith(".js") && !EXCLUDE_FILES.has(f));
let violations = [];
for (const f of files) {
  const src = fs.readFileSync(path.join(root, f), "utf8");
  violations = violations.concat(findViolations(f, src), findInPageViolations(f, src));
}

if (violations.length) {
  console.error(
    `PAGE_EVAL SEAM VIOLATION — every .evaluate()/.count() in runtime/app/ must route through ` +
    `page_eval.js's evalOn/countOn (EXEC-34), and an evalOn() function must come from page_scripts.js:\n` + violations.map((v) => `  - ${v}`).join("\n")
  );
  process.exit(1);
}

console.log(`check_page_eval_seam: OK — ${files.length} module(s) scanned, no bare .evaluate()/.count() calls.`);
