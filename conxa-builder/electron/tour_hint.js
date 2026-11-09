// Guided-tour note drawn inside the practice/sign-in page. Runs in an isolated world; the page's own
// scripts never see it. Injected only by browser_panel.js (sign-in runs on the Conxa website host).
function __conxaTourHint(cfg) {
  if (window.__conxaTourHintClear) window.__conxaTourHintClear();
  const SEL = cfg.target ? `[data-practice="${cfg.target}"], ${cfg.target}` : "";
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;inset:0;";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML =
    '<style>.n{position:fixed;max-width:260px;background:#22d3ee;color:#06080b;font:600 14px/1.35 system-ui,sans-serif;' +
    'padding:10px 14px;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.4);pointer-events:none}' +
    '.r{position:fixed;border:3px solid #22d3ee;border-radius:10px;pointer-events:none}</style>' +
    '<div class="r" hidden></div><div class="n"></div>';
  const note = root.querySelector(".n");
  const ring = root.querySelector(".r");
  note.textContent = cfg.text;
  const find = () => { try { return SEL ? document.querySelector(SEL) : null; } catch (_) { return null; } };
  const place = () => {
    const el = find();
    if (!el) { ring.hidden = true; note.style.left = "50%"; note.style.top = "16px"; note.style.transform = "translateX(-50%)"; return; }
    const r = el.getBoundingClientRect();
    ring.hidden = false;
    Object.assign(ring.style, { left: r.left - 4 + "px", top: r.top - 4 + "px", width: r.width + 8 + "px", height: r.height + 8 + "px" });
    note.style.transform = "";
    note.style.left = Math.max(8, Math.min(r.left, innerWidth - 280)) + "px";
    note.style.top = (r.bottom + 14 + 60 > innerHeight ? Math.max(8, r.top - 70) : r.bottom + 14) + "px";
  };
  // Blocks clicks/keys outside the highlighted element. Untargeted hints (e.g. the sign-in card) block nothing.
  const guard = (e) => {
    const el = find();
    if (!el || el.contains(e.target)) return;
    e.stopImmediatePropagation(); e.preventDefault();
  };
  const evs = ["click", "mousedown", "mouseup", "dblclick", "submit"];
  if (cfg.target) evs.forEach((n) => window.addEventListener(n, guard, true));
  const timer = setInterval(place, 250);
  window.addEventListener("resize", place); window.addEventListener("scroll", place, true);
  document.documentElement.appendChild(host);
  place();
  window.__conxaTourHintClear = () => {
    clearInterval(timer);
    evs.forEach((n) => window.removeEventListener(n, guard, true));
    window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true);
    host.remove(); window.__conxaTourHintClear = null;
  };
}
