/**
 * In-page capture bridge: runs inside the browser context.
 * Serializes the active element / event target and calls the Playwright binding
 * `__skillReport` with a JSON-serializable payload.
 */
(() => {
  if (window.__SKILL_BRIDGE_V1__) return;
  window.__SKILL_BRIDGE_V1__ = true;

  const TRACE = !!(typeof window !== "undefined" && window.__SKILL_TRACE__);

  // Lightweight trace: fires only when __SKILL_TRACE__ is set.
  // Goes direct to __skillReport (or postMessage relay) — never calls report() to avoid recursion.
  function trace(kind, extra) {
    if (!TRACE) return;
    const t = { _trace: true, kind: String(kind), url: location.href, ts: Date.now() };
    if (extra) t.extra = extra;
    const fn = window["__skillReport"];
    if (typeof fn === "function") {
      try { fn(t); } catch (_e) {}
      return;
    }
    if (window !== window.top) {
      try { window.parent.postMessage({ __skillBridgeRelay__: true, payload: t }, "*"); } catch (_e) {}
    }
  }

  trace("bridge_init", { isTop: window === window.top, hasBinding: typeof window["__skillReport"] === "function" });

  // Diagnostic: list iframes that already exist when the bridge initialises in this document.
  if (TRACE) {
    try {
      const iframes = Array.from(document.querySelectorAll("iframe")).slice(0, 32).map(function(f) {
        return { src: f.getAttribute("src") || "", id: f.id || "", testId: f.getAttribute("data-test-id") || "", name: f.getAttribute("name") || "" };
      });
      trace("iframes_at_init", { count: iframes.length, items: iframes });
    } catch (_e) {}
  }

  // Diagnostic: watch for <iframe> elements added to this document after init.
  if (TRACE) {
    try {
      const mo = new MutationObserver(function(records) {
        for (var i = 0; i < records.length; i++) {
          var addedNodes = records[i].addedNodes;
          for (var j = 0; j < addedNodes.length; j++) {
            var node = addedNodes[j];
            if (node && node.nodeType === 1 && node.tagName === "IFRAME") {
              trace("iframe_added", { src: node.getAttribute("src") || "", id: node.id || "", testId: node.getAttribute("data-test-id") || "" });
            }
          }
        }
      });
      mo.observe(document.documentElement || document, { childList: true, subtree: true });
    } catch (_e) {}
  }

  // Document-scoped listener registry: HubSpot's micro-frontend pattern replaces iframe
  // document content via document.open()/write() instead of navigating a new document, which
  // means add_init_script (real-navigation-only) never re-runs and the old, wiped-out listener
  // set is gone. Collecting registrations here (instead of registering them directly at each
  // addEventListener call-site below) lets installDocumentListeners() re-attach the exact same
  // set on demand — once at initial bridge init, and again from the open/write/writeln patch
  // below every time the document's content is swapped.
  const _docListeners = [];
  function onDoc(type, handler, options) {
    _docListeners.push([type, handler, options]);
  }
  function installDocumentListeners() {
    for (const [type, handler, options] of _docListeners) {
      try { document.removeEventListener(type, handler, options); } catch (_e) {}
      document.addEventListener(type, handler, options);
    }
    // Kept for the pump loop's diagnostics/backstop (see session.py) — no longer the sole
    // signal a reinstall is needed; the prototype patch below is now primary.
    document.__SKILL_BRIDGE_DOC_V1__ = true;
    // Return value lets session.py's reinstall-backstop call confirm real listeners were
    // actually (re)attached, not just that this function ran without throwing — a frame whose
    // own onDoc(...) registrations never ran (e.g. the script errored before reaching them)
    // would otherwise report success while attaching zero listeners.
    return _docListeners.length;
  }
  // Exposed so session.py's pump-loop backstop can trigger a reinstall directly instead of
  // re-running (and silently no-oping on) the whole bridge script.
  window.__SKILL_REINSTALL_DOC__ = installDocumentListeners;

  // Always-on (not TRACE-gated): patch Document.prototype.open/write/writeln so a document
  // content swap re-attaches our listeners immediately, synchronously, in-page — no round trip,
  // no poll cadence, no race window. Patched on the prototype (not the document instance)
  // because an instance-level override is exactly what document.open() discards; the prototype
  // belongs to the persistent window realm and survives the swap.
  if (document && !document.__SKILL_OPEN_HOOKED__) {
    try {
      document.__SKILL_OPEN_HOOKED__ = true;
      const proto = Document.prototype;
      const _origOpen = proto.open;
      const _origWrite = proto.write;
      const _origWriteln = proto.writeln;
      proto.open = function () {
        trace("document_open", { url: location.href });
        const ret = _origOpen.apply(this, arguments);
        installDocumentListeners();
        return ret;
      };
      proto.write = function (s) {
        trace("document_write", { len: (s && s.length) || 0 });
        const ret = _origWrite.apply(this, arguments);
        installDocumentListeners();
        return ret;
      };
      proto.writeln = function (s) {
        trace("document_write", { len: (s && s.length) || 0 });
        const ret = _origWriteln.apply(this, arguments);
        installDocumentListeners();
        return ret;
      };
    } catch (_e) {}
  }

  // Recursive relay: every frame (not just top) listens for __skillBridgeRelay__ messages
  // from child iframes, applies that child's bbox offset, then either delivers to
  // __skillReport (top frame) or forwards to its own parent (intermediate frames).
  // This handles arbitrarily nested cross-origin iframe chains (top → A → B → …).
  window.addEventListener("message", function (ev) {
    if (!ev.data || !ev.data.__skillBridgeRelay__) return;
    const relayPayload = ev.data.payload;
    try {
      const iframes = document.querySelectorAll("iframe");
      for (const f of iframes) {
        if (f.contentWindow === ev.source) {
          const r = f.getBoundingClientRect();
          const vp = relayPayload.visual_placeholder;
          if (vp && vp.bbox) {
            vp.bbox.x = (vp.bbox.x || 0) + Math.round(r.left);
            vp.bbox.y = (vp.bbox.y || 0) + Math.round(r.top);
          }
          break;
        }
      }
    } catch (_e) {}
    if (window === window.top) {
      const fn = window["__skillReport"];
      if (typeof fn === "function") fn(relayPayload);
    } else {
      try {
        window.parent.postMessage({ __skillBridgeRelay__: true, payload: relayPayload }, "*");
      } catch (_e) {}
    }
  });

  const CAP =
    typeof window !== "undefined" && window.__SKILL_CAPTURE_PROFILE__
      ? window.__SKILL_CAPTURE_PROFILE__
      : {};
  const cssDepthMax = Number(CAP.css_path_max_depth) > 0 ? Number(CAP.css_path_max_depth) : 8;
  const xpathDepthMax = Number(CAP.xpath_max_depth) > 0 ? Number(CAP.xpath_max_depth) : 10;
  const anchorCandMax = Number(CAP.anchor_candidates_max) > 0 ? Number(CAP.anchor_candidates_max) : 40;
  const classSliceMax = Number(CAP.class_slice_max) >= 0 ? Number(CAP.class_slice_max) : 2;
  const safeTextMaxEl = Number(CAP.safe_text_max) > 0 ? Number(CAP.safe_text_max) : 120;
  const pageFpMax = Number(CAP.page_fingerprint_slice) > 0 ? Number(CAP.page_fingerprint_slice) : 4000;
  const siblingsMax = Number(CAP.siblings_summarize_max) > 0 ? Number(CAP.siblings_summarize_max) : 6;
  const inputDebounceMs = Number(CAP.input_debounce_ms) > 0 ? Number(CAP.input_debounce_ms) : 350;
  const scrollDebounceMs = Number(CAP.scroll_debounce_ms) > 0 ? Number(CAP.scroll_debounce_ms) : 220;
  const clickSettleQuietMs = Number(CAP.click_settle_quiet_ms) > 0 ? Number(CAP.click_settle_quiet_ms) : 20;
  const clickSettleMaxMs = Number(CAP.click_settle_max_ms) > 0 ? Number(CAP.click_settle_max_ms) : 250;
  function djb2(str) {
    let hash = 5381;
    for (let i = 0; i < str.length; i++) {
      hash = (hash * 33) ^ str.charCodeAt(i);
    }
    return (hash >>> 0).toString(16);
  }

  function pageFingerprint() {
    const href = location.href;
    const title = document.title || "";
    const text = (document.documentElement && document.documentElement.innerText) || "";
    const norm = text.replace(/\s+/g, " ").trim().slice(0, pageFpMax);
    return `${href}|${title}|${djb2(norm)}`;
  }

  function safeText(el, maxLen) {
    if (!el || !el.innerText) return "";
    return String(el.innerText).replace(/\s+/g, " ").trim().slice(0, maxLen);
  }

  // Walks getRootNode().host repeatedly to collect every shadow boundary crossed on the way up
  // from el. A component like <sl-button>Primary</sl-button> renders a shadow-internal
  // <button> whose own innerText/aria-label are empty — the visible label only exists on the
  // light-DOM host — so callers needing the element's real accessible name/text must fall back
  // to shadowHostChain(el)[0].host once own-element signals come back empty.
  function shadowHostChain(el) {
    const chain = [];
    let node = el;
    let hops = 0;
    while (node && hops < 10) {
      const root = typeof node.getRootNode === "function" ? node.getRootNode() : null;
      if (!root || !root.host) break;
      const host = root.host;
      const tag = (host.tagName || "").toLowerCase();
      chain.push({ host: tag + (host.id ? `#${host.id}` : ""), mode: root.mode || "open" });
      node = host;
      hops += 1;
    }
    return chain;
  }

  function nodeAsElement(node) {
    if (!node) return null;
    if (node.nodeType === 1) return node;
    if (node.parentElement && node.parentElement.nodeType === 1) return node.parentElement;
    if (node.host && node.host.nodeType === 1) return node.host;
    return null;
  }

  function eventTargetFromPath(ev) {
    if (ev && typeof ev.composedPath === "function") {
      const path = ev.composedPath();
      for (const node of path) {
        const el = nodeAsElement(node);
        if (el && el !== document.documentElement && el !== document.body) return el;
      }
    }
    return nodeAsElement(ev && ev.target);
  }

  function parentOrHost(el) {
    if (!el || el.nodeType !== 1) return null;
    return el.parentElement || (el.getRootNode && el.getRootNode().host) || null;
  }

  function cssEscapeIdent(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => "\\" + c);
  }

  function nodeRole(n) {
    return ((n && n.getAttribute && n.getAttribute("role")) || "").toLowerCase();
  }

  function inputTypeOf(el) {
    return ((el && el.getAttribute && el.getAttribute("type")) || "").toLowerCase();
  }

  function isTextInputType(type) {
    const t = (type || "text").toLowerCase();
    return ["button", "checkbox", "color", "file", "hidden", "image", "radio", "range", "reset", "submit"].indexOf(t) < 0;
  }

  function isContentEditableNode(el) {
    if (!el || !el.getAttribute) return false;
    const attr = el.getAttribute("contenteditable");
    return el.isContentEditable || attr === "" || attr === "true" || attr === "plaintext-only";
  }

  function isEditableNode(el) {
    if (!el || el.nodeType !== 1) return false;
    const tag = el.tagName.toLowerCase();
    if (tag === "textarea" || tag === "select") return true;
    if (tag === "input") return isTextInputType(inputTypeOf(el));
    if (isContentEditableNode(el)) return true;
    const r = nodeRole(el);
    return r === "textbox" || r === "searchbox" || r === "combobox";
  }

  function findEditableDescendant(root, depth) {
    if (!root || depth > 3) return null;
    if (root.nodeType !== 1 && root.nodeType !== 11) return null;
    if (root.shadowRoot) {
      const shadowHit = findEditableDescendant(root.shadowRoot, depth + 1);
      if (shadowHit) return shadowHit;
    }
    const queryRoot = root.querySelectorAll ? root : null;
    if (!queryRoot) return null;
    const candidates = queryRoot.querySelectorAll(
      'input,textarea,select,[contenteditable],[role="textbox"],[role="searchbox"],[role="combobox"]'
    );
    for (const candidate of candidates) {
      if (isEditableNode(candidate)) return candidate;
      if (candidate.shadowRoot) {
        const shadowNested = findEditableDescendant(candidate.shadowRoot, depth + 1);
        if (shadowNested) return shadowNested;
      }
    }
    return null;
  }

  function resolveEditableTarget(el) {
    if (!el || el.nodeType !== 1) return null;
    if (isEditableNode(el)) {
      if (nodeRole(el) === "combobox") {
        return findEditableDescendant(el, 0) || el;
      }
      return el;
    }
    const direct = findEditableDescendant(el, 0);
    if (direct) return direct;
    let cur = parentOrHost(el);
    for (let depth = 0; depth < 14 && cur; depth++) {
      if (isEditableNode(cur)) {
        if (nodeRole(cur) === "combobox") {
          return findEditableDescendant(cur, 0) || cur;
        }
        return cur;
      }
      if (nodeRole(cur) === "combobox") {
        const nested = findEditableDescendant(cur, 0);
        if (nested) return nested;
      }
      const tag = cur.tagName ? cur.tagName.toLowerCase() : "";
      if (tag === "body" || tag === "html") break;
      cur = parentOrHost(cur);
    }
    return null;
  }

  function readEditableValue(el) {
    if (!el) return "";
    const nested = nodeRole(el) === "combobox" ? findEditableDescendant(el, 0) : null;
    const target = nested || el;
    if ("value" in target) return target.value == null ? "" : String(target.value);
    const ariaValue = target.getAttribute && (target.getAttribute("aria-valuetext") || target.getAttribute("aria-value"));
    if (ariaValue != null) return String(ariaValue);
    return String(target.innerText || target.textContent || "");
  }

  function isSensitiveEditable(el) {
    if (!el || !el.getAttribute) return false;
    if (inputTypeOf(el) === "password") return true;
    const haystack = [
      el.getAttribute("autocomplete"),
      el.getAttribute("name"),
      el.getAttribute("id"),
      el.getAttribute("aria-label"),
      el.getAttribute("placeholder"),
    ].join(" ").toLowerCase();
    return /\b(pass(word|code|phrase)?|otp|secret)\b/.test(haystack);
  }

  function editableComparisonValue(el) {
    const raw = readEditableValue(el);
    if (isSensitiveEditable(el)) return `sensitive:${String(raw).length}`;
    return String(raw);
  }

  // ARIA roles that name an INDIVIDUAL activatable control or item — the thing a user actually
  // clicks. Container roles (grid, listbox, menu, tree, radiogroup) are deliberately absent: a
  // click lands on the item, and treating the container as the target would record the wrong
  // element. `gridcell` is what a calendar day cell carries (react-datepicker renders
  // `<div role="gridcell" tabindex="-1">`); without it, resolveMeaningfulTarget walked past the
  // day, found no interactive ancestor and returned null, so the click that actually COMMITS a
  // date was never recorded. The run replayed as "open picker, change year, change month" with
  // no day pick — the calendar visibly moved and the field kept its original date. The
  // tabindex check below cannot cover these: roving-tabindex widgets give every unfocused item
  // tabindex="-1" by design.
  const INTERACTIVE_ROLES = [
    "button", "link", "textbox", "searchbox", "checkbox", "radio", "switch", "tab",
    "menuitem", "menuitemcheckbox", "menuitemradio", "option", "combobox",
    "gridcell", "treeitem", "spinbutton", "slider",
  ];

  // A container that only qualifies via the tabindex fallback below (line ~357) can still be
  // the WRONG target: jQuery UI's Menu widget puts tabindex="0" on the <ul> itself while each
  // item's clickable node is tabindex="-1" (isInteractiveNode rejects it), so the walk in
  // resolveMeaningfulTarget would otherwise return the whole list instead of the clicked item.
  // When that happens, prefer the nearest item ancestor of the ORIGINAL click target.
  const ITEM_SELECTOR = "li,[role=option],[role=menuitem],[role=menuitemradio],[role=menuitemcheckbox],[role=treeitem],[role=gridcell]";
  const CONTAINER_SELECTOR = "ul,ol,[role=listbox],[role=menu],[role=menubar],[role=tree],[role=grid],[role=radiogroup]";

  function isInteractiveNode(n) {
    if (!n || n.nodeType !== 1) return false;
    const tag = n.tagName.toLowerCase();
    if (tag === "button" || tag === "a" || tag === "input" || tag === "textarea" || tag === "select") {
      return true;
    }
    const r = ((n.getAttribute && n.getAttribute("role")) || "").toLowerCase();
    if (INTERACTIVE_ROLES.indexOf(r) >= 0) {
      return true;
    }
    if (tag === "label" && n.htmlFor) return true;
    if (isEditableNode(n)) return true;
    if (n.hasAttribute && (n.hasAttribute("onclick") || n.hasAttribute("aria-haspopup") || n.hasAttribute("aria-expanded") || n.hasAttribute("aria-controls"))) {
      return true;
    }
    const tabIndex = n.getAttribute && n.getAttribute("tabindex");
    if (tabIndex !== null && tabIndex !== "-1") return true;
    if ((tag === "div" || tag === "span") && n.getAttribute && n.getAttribute("data-action")) return true;
    return false;
  }

  /** Resolve clicks on svg/path/shallow divs to the control the user meant (button, link, input, …). */
  function resolveMeaningfulTarget(el) {
    if (!el || el.nodeType !== 1) return null;
    let cur = el;
    for (let depth = 0; depth < 14 && cur; depth++) {
      if (isInteractiveNode(cur)) {
        if (cur.matches && cur.matches(CONTAINER_SELECTOR)) {
          // The tabindex fallback in isInteractiveNode can accept a list/menu container
          // (e.g. jQuery UI's <ul tabindex="0">) whose items are tabindex="-1" and match
          // nothing themselves. Prefer the clicked item within it, if there is one.
          const item = el.closest && el.closest(ITEM_SELECTOR);
          if (item && cur.contains(item)) return item;
        }
        return cur;
      }
      const tag = cur.tagName ? cur.tagName.toLowerCase() : "";
      if (tag === "body" || tag === "html") break;
      cur = parentOrHost(cur);
    }
    return null;
  }

  function buildCssPath(el) {
    if (!el || el.nodeType !== 1) return "";
    const parts = [];
    let cur = el;
    let depth = 0;
    while (cur && cur.nodeType === 1 && depth < cssDepthMax) {
      let part = cur.tagName.toLowerCase();
      if (cur.id) {
        part = "#" + cssEscapeIdent(cur.id);
        parts.unshift(part);
        break;
      }
      if (cur.classList && cur.classList.length) {
        const cls = Array.from(cur.classList)
          .slice(0, classSliceMax)
          .map((c) => "." + cssEscapeIdent(c))
          .join("");
        part += cls;
      }
      const parent = cur.parentElement;
      if (parent) {
        const sameTagSiblings = Array.from(parent.children).filter(
          (n) => n.tagName === cur.tagName
        );
        if (sameTagSiblings.length > 1) {
          const idx = sameTagSiblings.indexOf(cur) + 1;
          part += `:nth-of-type(${idx})`;
        }
      }
      parts.unshift(part);
      cur = parent;
      depth++;
    }
    return parts.join(" > ");
  }

  function buildXPath(el) {
    if (!el || el.nodeType !== 1) return "";
    const segs = [];
    let node = el;
    let depth = 0;
    while (node && node.nodeType === 1 && depth < xpathDepthMax) {
      let ix = 1;
      let sib = node.previousElementSibling;
      while (sib) {
        if (sib.tagName === node.tagName) ix++;
        sib = sib.previousElementSibling;
      }
      segs.unshift(`${node.tagName.toLowerCase()}[${ix}]`);
      node = node.parentElement;
      depth++;
    }
    // `/a/b/c` is an ABSOLUTE XPath — it only matches when the first segment is the document
    // element. The walk above stops at xpathDepthMax, so for any element deeper than the cap we
    // were emitting a relative path wearing an absolute prefix, which matches nothing, ever
    // (react-datepicker's year <select> compiled to `/div[2]/div[2]/…`: 0 matches). Only claim
    // absolute when we actually reached the root; otherwise `//` anchors it as a descendant path.
    const reachedRoot = !node || node.nodeType !== 1;
    return (reachedRoot ? "/" : "//") + segs.join("/");
  }

  // A <select>'s innerText is its concatenated <option> list, not visible page text. Playwright's
  // text engine never matches a <select> by its options, so a text selector built from one is a
  // guaranteed miss that still occupies the bundle's highest-durability slot. Same reasoning as
  // identity_bundle.py's _NAME_FROM_CONTENT_ROLES gate, applied to the text channel.
  const OPTION_CONTENT_TAGS = { select: 1, datalist: 1, optgroup: 1 };

  function buildTextSelector(el) {
    if (!el || !el.tagName) return "";
    if (OPTION_CONTENT_TAGS[el.tagName.toLowerCase()]) return "";
    // Read one char past the cap so an over-long text is detectable rather than silently cut.
    const raw = safeText(el, 81);
    if (!raw) return "";
    // `text="…"` is an EXACT match in Playwright's grammar, so a truncated string can never
    // match the element it came from. Emit nothing rather than a selector that cannot resolve.
    if (raw.length > 80) return "";
    const esc = raw.replace(/"/g, '\\"');
    return `text="${esc}"`;
  }

  // Bounded identity attributes for IdentityBundle's named-attr engine. Mirrors
  // compiler/named_attrs.py + stable_hash._SKIP_ATTRS: role + stable data-*, never
  // testid (separate engine) or transient state. Cap keeps the event payload small.
  const IDENTITY_SKIP_ATTRS = {
    "class": 1, "style": 1, "tabindex": 1,
    "aria-expanded": 1, "aria-selected": 1, "aria-checked": 1, "aria-disabled": 1,
    "aria-pressed": 1, "aria-current": 1, "aria-busy": 1,
    "data-state": 1, "data-active": 1, "data-focus": 1, "data-open": 1,
  };
  const IDENTITY_TESTID_ATTRS = { "data-testid": 1, "data-test-id": 1, "data-test": 1, "data-cy": 1 };
  const IDENTITY_ATTR_CAP = 12;
  const IDENTITY_ATTR_MAX_VALUE = 64;
  const IDENTITY_UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const IDENTITY_LONG_HEX_RE = /^[0-9a-f]{16,}$/i;
  const IDENTITY_ATTR_PREF = ["role", "data-key", "data-command", "data-action", "data-value"];

  function isStableAttrValue(v) {
    if (!v || v.length > IDENTITY_ATTR_MAX_VALUE) return false;
    if (IDENTITY_UUID_RE.test(v) || IDENTITY_LONG_HEX_RE.test(v)) return false;
    if (
      v.length >= 16 && !/\s/.test(v) &&
      /[A-Za-z]/.test(v) && /\d/.test(v) && /[A-Z]/.test(v) && /[a-z]/.test(v) &&
      /^[A-Za-z0-9_\-]+$/.test(v)
    ) {
      return false;
    }
    return true;
  }

  function collectIdentityAttrs(el) {
    const pending = [];
    if (!el || !el.attributes) return {};
    for (let i = 0; i < el.attributes.length; i++) {
      const a = el.attributes[i];
      const k = String(a.name || "").toLowerCase();
      if (IDENTITY_SKIP_ATTRS[k] || IDENTITY_TESTID_ATTRS[k]) continue;
      if (k !== "role" && k.indexOf("data-") !== 0) continue;
      const v = String(a.value || "").trim();
      if (!isStableAttrValue(v)) continue;
      pending.push([k, v]);
    }
    pending.sort((a, b) => {
      const ia = IDENTITY_ATTR_PREF.indexOf(a[0]);
      const ib = IDENTITY_ATTR_PREF.indexOf(b[0]);
      const ra = ia === -1 ? 99 : ia;
      const rb = ib === -1 ? 99 : ib;
      if (ra !== rb) return ra - rb;
      return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0;
    });
    const out = {};
    for (let i = 0; i < pending.length && i < IDENTITY_ATTR_CAP; i++) {
      out[pending[i][0]] = pending[i][1];
    }
    return out;
  }

  // Stable selector: data-testid > aria-label > name > placeholder > text
  // Priority order matches runtime fingerprint scoring weights.
  function buildStableSelector(el) {
    const testIdAttr =
      el.hasAttribute("data-testid")  ? "data-testid"  :
      el.hasAttribute("data-test-id") ? "data-test-id" :
      el.hasAttribute("data-test")    ? "data-test"    :
      el.hasAttribute("data-cy")      ? "data-cy"      : null;
    if (testIdAttr) { const testId = el.getAttribute(testIdAttr); return `[${testIdAttr}="${testId}"]`; }
    const ariaLabel = el.getAttribute("aria-label");
    if (ariaLabel) return `[aria-label="${ariaLabel.replace(/"/g, '\\"')}"]`;
    const name = el.getAttribute("name");
    if (name) {
      const tag = el.tagName.toLowerCase();
      return `${tag}[name="${name.replace(/"/g, '\\"')}"]`;
    }
    return null;
  }

  function implicitAriaRole(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === "input") {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      if (t === "checkbox") return "checkbox";
      if (t === "radio") return "radio";
      if (t === "range") return "slider";
      if (t === "submit" || t === "button" || t === "reset") return "button";
      if (t === "search") return "searchbox";
      if (t === "number") return "spinbutton";
      return "textbox";
    }
    if (tag === "textarea") return "textbox";
    if (tag === "select") return el.hasAttribute("multiple") ? "listbox" : "combobox";
    if (tag === "button") return "button";
    if (tag === "a" && el.hasAttribute("href")) return "link";
    return null;
  }

  function buildAriaSelector(el) {
    // Try stable selector first
    const stable = buildStableSelector(el);
    if (stable) return stable;
    const explicitRole = el.getAttribute("role");
    const nonSemanticTags = { path: 1, svg: 1, g: 1, div: 1, span: 1, input: 1, textarea: 1, select: 1 };
    const role = explicitRole || implicitAriaRole(el);
    if (!role || nonSemanticTags[role]) return null;
    // Do NOT use safeText here: CSS [name=...] matches the HTML name attribute,
    // not element text content. Using text content produced selectors like
    // [role="button"][name="New"] that never match native <button> elements
    // whose role is implicit and whose text is not a name= attribute.
    // Text-content-based resolution is handled by the text_based and role
    // signals in identity_bundle (compiler/identity_bundle.py).
    const name =
      el.getAttribute("aria-label") ||
      el.getAttribute("name") ||
      el.getAttribute("placeholder");
    if (!name) return null;
    const esc = name.replace(/"/g, '\\"');
    return `[role="${role}"][name="${esc}"]`;
  }

  function nearestForm(el) {
    let n = el;
    while (n) {
      if (n.tagName && n.tagName.toLowerCase() === "form") {
        const id = n.id ? "#" + n.id : "";
        const nm = n.getAttribute("name") || "";
        return `form${id}${nm ? "[name=" + JSON.stringify(nm) + "]" : ""}`;
      }
      n = n.parentElement;
    }
    return null;
  }

  function parentSummary(el) {
    const p = el && el.parentElement;
    if (!p) return "";
    const tag = p.tagName.toLowerCase();
    const id = p.id ? "#" + p.id : "";
    const role = p.getAttribute("role");
    const r = role ? `[role=${role}]` : "";
    return `${tag}${id}${r}`;
  }

  function siblingSummaries(el, limit) {
    const p = el && el.parentElement;
    if (!p) return [];
    const out = [];
    for (const c of p.children) {
      if (c === el) continue;
      if (c.nodeType !== 1) continue;
      const tag = c.tagName.toLowerCase();
      const tid = c.id ? "#" + c.id : "";
      const txt = safeText(c, 40);
      out.push(`${tag}${tid}:${txt}`);
      if (out.length >= limit) break;
    }
    return out;
  }

  function indexInParent(el) {
    const p = el && el.parentElement;
    if (!p) return 0;
    return Array.prototype.indexOf.call(p.children, el);
  }

  const _SKIP_INTERACTIVE = new Set(["input", "button", "select", "textarea", "a", "option"]);

  function captureAssociatedLabel(el) {
    // 1. Standard: <label for="id">
    if (el.id) {
      try {
        const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
        if (lab) return safeText(lab, 80);
      } catch (_) {}
    }
    // 2. Element nested inside <label>
    const ancestor = el.closest("label");
    if (ancestor) return safeText(ancestor, 80);
    // 3. aria-labelledby reference
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const ref = document.getElementById(labelledBy);
      if (ref) return safeText(ref, 80);
    }
    // 4. <label> sibling in parent
    const parent = el.parentElement;
    if (parent) {
      const lab = Array.from(parent.querySelectorAll("label")).find(l => l !== el && !l.contains(el));
      if (lab) return safeText(lab, 80);
      // 5. <label> sibling in grandparent
      const gp = parent.parentElement;
      if (gp) {
        const gpLab = Array.from(gp.querySelectorAll("label")).find(l => !l.contains(el));
        if (gpLab) return safeText(gpLab, 80);
      }
    }
    // 6. Walk up 4 levels — find nearest preceding sibling with short visible text.
    // Handles apps (e.g. Pipedrive) that use <div>/<span> as visual labels instead of <label>.
    let node = el;
    for (let depth = 0; depth < 8; depth++) {
      const container = node.parentElement;
      if (!container) break;
      const children = Array.from(container.children);
      const nodeIdx = children.indexOf(node);
      for (let i = nodeIdx - 1; i >= 0; i--) {
        const sib = children[i];
        if (!sib || sib.contains(el)) continue;
        if (_SKIP_INTERACTIVE.has(sib.tagName.toLowerCase())) continue;
        // A sibling that itself CONTAINS a live control (a react-select-style wrapper <div>
        // around a hidden <input>, an ARIA combobox/listbox, a contenteditable) is that OTHER
        // field's own container, not a caption for this one -- its visible text is that other
        // field's currently selected/typed value, which changes independently of this element
        // and has nothing to do with what this element is. Concretely: a "City" input recorded
        // right after a "State" combobox picked up the state's rendered value ("Uttar Pradesh")
        // as its own label_text, because _SKIP_INTERACTIVE only caught a bare <input> sibling,
        // never a wrapper <div> around one (audit finding, mega-workflow investigation).
        if (sib.querySelector && sib.querySelector('input,select,textarea,[contenteditable],[role="combobox"],[role="listbox"]')) continue;
        const txt = safeText(sib, 80);
        // A sibling whose entire text is a month+year string ("August 2026") is never a real
        // field label — it's a live "current value" readout (a calendar widget's own header is
        // the common case, e.g. react-datepicker's month/year <select> dropdowns sit right next
        // to one), and it re-renders as the user interacts, so a captured label like
        // "august_2026" would even change mid-interaction. Skip it and keep walking.
        if (txt && _MONTH_YEAR_EXACT_RE.test(txt.trim())) continue;
        if (txt && txt.length >= 2 && txt.length <= 60) return txt;
      }
      node = container;
    }
    return null;
  }

  function pickAnchors(el) {
    const anchors = [];
    const rect = el.getBoundingClientRect();
    const candidates = Array.from(
      document.querySelectorAll(
        "main,nav,header,footer,[role=main],[role=navigation],[role=dialog],[aria-modal=true],h1,h2,h3,[data-testid],[data-section-title]"
      )
    ).slice(0, anchorCandMax);
    for (const c of candidates) {
      if (!c || c === el) continue;
      const r = c.getBoundingClientRect();
      let relation = "inside";
      if (c.contains && c.contains(el)) {
        relation = "inside";
      } else if (rect.top >= r.bottom) {
        relation = "above";
      } else if (rect.bottom <= r.top) {
        relation = "below";
      } else {
        relation = "inside";
      }
      const label = (
        c.getAttribute("aria-label") ||
        safeText(c, 60) ||
        c.tagName.toLowerCase()
      ).slice(0, 120);
      anchors.push({ element: label, relation });
      if (anchors.length >= 4) break;
    }
    if (!anchors.length && el.parentElement) {
      anchors.push({ element: parentSummary(el), relation: "inside" });
    }
    return anchors;
  }

  function normalizedText(el) {
    return safeText(el, 500).toLowerCase();
  }

  // Phase 2: capture full ancestor chain up to <body> for compile-time LLM context.
  // Returns array of {tag, id, classes, outer_html} from immediate parent up.
  function captureAncestors(el, maxDepth) {
    const max = maxDepth || 32;
    const out = [];
    let cur = el && el.parentElement;
    let depth = 0;
    // Detect if we're in a cross-origin frame by trying to access parent.location
    let isCrossOrigin = false;
    try {
      void window.parent.location.href;
    } catch (_e) {
      isCrossOrigin = true;
    }
    while (cur && cur.nodeType === 1 && depth < max) {
      const tag = (cur.tagName || "").toLowerCase();
      const id = cur.id || "";
      const classes = cur.classList ? Array.from(cur.classList).slice(0, 32) : [];
      // outer_html truncated to keep payload bounded; LLM compiler can request full blob if needed.
      let oh = "";
      let outerHtmlError = null;
      try {
        oh = (cur.outerHTML || "").slice(0, 2000);
      } catch (_e) {
        outerHtmlError = `cross_origin_iframe`;
      }
      const ancestor = { tag: tag, id: id, classes: classes, outer_html: oh };
      if (isCrossOrigin && depth === 0) {
        ancestor.cross_origin = true;
      }
      if (outerHtmlError) {
        ancestor.outer_html_error = outerHtmlError;
      }
      out.push(ancestor);
      if (tag === "body" || tag === "html") break;
      cur = cur.parentElement;
      depth++;
    }
    return out;
  }

  // Phase 2: extract visible text within a pixel radius of the element bbox.
  // Used by the LLM compiler to anchor selectors against nearby labels/headers.
  function captureSurroundingText(el, radiusPx) {
    const r = radiusPx || 200;
    const rect = el && el.getBoundingClientRect ? el.getBoundingClientRect() : null;
    if (!rect) return "";
    const elemTop = rect.top - r;
    const elemBot = rect.bottom + r;
    const elemLeft = rect.left - r;
    const elemRight = rect.right + r;
    const elemCenterX = (rect.left + rect.right) / 2;
    const elemCenterY = (rect.top + rect.bottom) / 2;
    const maxDist = 500;
    // Walk text nodes in the document; cheap heuristic for "near" via getBoundingClientRect of parent.
    const out = [];
    const totalCharBudget = 1500;
    const maxNodes = 2000;
    let used = 0;
    let nodeCount = 0;
    try {
      const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT, null);
      let node;
      while ((node = walker.nextNode()) && used < totalCharBudget && nodeCount < maxNodes) {
        nodeCount++;
        const text = (node.nodeValue || "").trim();
        if (text.length < 2) continue;
        const parent = node.parentElement;
        if (!parent) continue;
        if (parent === el || el.contains(parent)) continue;
        let pr;
        try { pr = parent.getBoundingClientRect(); } catch (_e) { continue; }
        if (!pr || (pr.width === 0 && pr.height === 0)) continue;
        if (pr.bottom < elemTop || pr.top > elemBot) continue;
        if (pr.right < elemLeft || pr.left > elemRight) continue;
        const prCenterX = (pr.left + pr.right) / 2;
        const prCenterY = (pr.top + pr.bottom) / 2;
        const dist = Math.sqrt(Math.pow(prCenterX - elemCenterX, 2) + Math.pow(prCenterY - elemCenterY, 2));
        if (dist > maxDist) continue;
        const chunk = text.slice(0, Math.min(200, totalCharBudget - used));
        out.push(chunk);
        used += chunk.length + 1;
      }
    } catch (_e) {}
    return out.join(" | ").slice(0, totalCharBudget);
  }

  // Phase 2: stable hash of the document's interactive surface for dedup-by-state.
  // Cheap djb2 over interactiveSignature() — the Python session computes the full sha256
  // from the captured outerHTML.
  function _domSignatureHash(sig) {
    let h = 5381;
    for (let i = 0; i < sig.length; i++) {
      h = ((h << 5) + h + sig.charCodeAt(i)) | 0;
    }
    return ("00000000" + (h >>> 0).toString(16)).slice(-8);
  }

  function intentHint(tag, type, role, _text) {
    const t = (type || "").toLowerCase();
    const r = (role || "").toLowerCase();
    if (t === "submit") return "commit_form";
    if (t === "search") return "search_query";
    if (r === "link" || tag === "a") return "navigate";
    if (tag === "button" || r === "button") return "activate_control";
    if (tag === "input" || tag === "textarea" || r === "textbox" || r === "searchbox") return "provide_input";
    if (tag === "select") return "choose_option";
    if (r === "combobox") return "choose_option";
    return "interact";
  }

  // DOM diff: lightweight snapshot of interactive element signatures for post-action comparison.
  // `root` scopes the query (defaults to the whole document) — used by conditional-state
  // detection below to fingerprint just the descendants of one container.
  const INTERACTIVE_SEL = 'button,a[href],input:not([type="hidden"]),select,textarea,[role="button"],[role="link"],[data-testid],[data-test-id]';

  function interactiveSignature(root) {
    const scope = root || document;
    const els = Array.from(scope.querySelectorAll(INTERACTIVE_SEL)).slice(0, 120);
    return els.map(el => {
      const tag = el.tagName.toLowerCase();
      const tid = el.getAttribute("data-testid") || el.getAttribute("data-test-id") || "";
      const al  = el.getAttribute("aria-label") || "";
      const txt = (el.innerText || el.value || "").trim().slice(0, 60);
      return `${tag}|${tid}|${al}|${txt}`;
    }).join("\n");
  }

  // Dedup-signature helper: interactiveSignature() truncates at 120 elements, so a portal-mounted
  // dialog appended past that cap (React portals mount as new children at the end of <body>) can
  // add or replace elements without the sliced text changing at all. The session-level snapshot
  // dedup (session.py's _capture_dom_snapshot_sync) then skips capturing a fresh DOM/a11y snapshot
  // for an event that actually happened inside a brand-new dialog. Folding in the *unsliced*
  // element count plus document.body's direct child count catches that case without changing
  // interactiveSignature()'s own return value — which is also line-diffed by _computeDomDiff for
  // dom_diff reporting, so it must stay stable.
  function _dedupSigExtra() {
    let total = 0;
    try { total = document.querySelectorAll(INTERACTIVE_SEL).length; } catch (_e) {}
    let bodyChildren = 0;
    try { bodyChildren = document.body ? document.body.childElementCount : 0; } catch (_e) {}
    return `${total}|${bodyChildren}`;
  }

  // --- Custom date-picker detection --------------------------------------------------------
  // Native <input type=date|datetime-local|time|month|week> already compiles correctly via the
  // "change" listener's date_pick path below (bridge.js's onDoc("change", ...)); this section
  // only tags *clicks* landing inside a custom calendar grid (MUI, react-datepicker, flatpickr,
  // Ant Design, jQuery UI, ...) so the compiler can later collapse the open→nav→day-cell click
  // run it would otherwise record into one parameterized date_pick step. Every heuristic below
  // fails closed to `null` — meaning "record this exactly like any other click", today's
  // behavior — so a widget shape this doesn't recognize is never worse off than before.
  const CALENDAR_ROOT_SELECTORS = [
    ".flatpickr-calendar", ".react-datepicker", "[class*='MuiDateCalendar']",
    "[class*='MuiPickersCalendar']", "[class*='MuiPickersDay']", ".ui-datepicker",
    "[class*='datepicker']", "[class*='DatePicker']", "[data-testid*='calendar']",
    "[class*='calendar']",
  ];
  const _MONTH_YEAR_RE = /[A-Za-z]{3,9}\.?\s+\d{4}|\d{4}\s+[A-Za-z]{3,9}/;
  // Anchored variant for captureAssociatedLabel's guard below: that check needs "this sibling's
  // ENTIRE text is a month+year readout" (skip it), not "contains one somewhere" (a legitimate
  // label like "Enter the August 2026 report ID" must not be discarded).
  const _MONTH_YEAR_EXACT_RE = /^[A-Za-z]{3,9}\.?\s+\d{4}$|^\d{4}\s+[A-Za-z]{3,9}$/;
  const _NAV_PREV_RE = /prev|previous|«|‹/i;
  const _NAV_NEXT_RE = /next|»|›/i;
  const _TIME_OPTION_RE = /^\d{1,2}:\d{2}(\s?[AaPp][Mm])?$/;

  function _isCalendarGridNode(node) {
    if (!node || node.nodeType !== 1 || !node.getAttribute) return false;
    const role = node.getAttribute("role") || "";
    if (role === "grid" || role === "application") {
      let cellCount = 0;
      try { cellCount = node.querySelectorAll('[role="gridcell"],td,[class*="day"]').length; } catch (_e) {}
      if (cellCount >= 20) return true;
    }
    for (const sel of CALENDAR_ROOT_SELECTORS) {
      try { if (node.matches(sel)) return true; } catch (_e) {}
    }
    return false;
  }

  function _findNavButton(root, dir) {
    const re = dir === "prev" ? _NAV_PREV_RE : _NAV_NEXT_RE;
    let candidates = [];
    try { candidates = Array.from(root.querySelectorAll('button,[role="button"],a,[aria-label],[title]')); } catch (_e) {}
    for (const c of candidates) {
      const label = (c.getAttribute("aria-label") || c.getAttribute("title") || safeText(c, 30) || "").trim();
      if (label && re.test(label)) return c;
    }
    return null;
  }

  /** Widget wrapper (header + nav + day grid). Widens past a bare role="grid" table that has no
   * nav buttons of its own to the nearest ancestor that does — jQuery UI-style widgets nest the
   * day-cell table one level below the real prev/next chrome. */
  function findCalendarRoot(el) {
    let cur = el;
    for (let depth = 0; depth < 6 && cur && cur.nodeType === 1; depth++) {
      if (_isCalendarGridNode(cur)) {
        // Take the OUTERMOST calendar node, not the first one matched. CALENDAR_ROOT_SELECTORS
        // matches on class substrings ("[class*='datepicker']"), and every descendant of a
        // widget repeats the library prefix in its own class — react-datepicker's day cell is
        // `react-datepicker__day`, which matches that selector itself. Stopping at the first hit
        // therefore returned the CELL as the "grid", so date_context.grid and .cell were the same
        // date-specific selector: the runtime scoped its day search INSIDE the recorded cell (a
        // cell has no day-number descendant, only its own text) and could never find any day but
        // the one recorded. Widening also picks up the header, prev/next and year/month selects,
        // which all live above the cell and were coming back empty.
        let root = cur;
        let up = cur.parentElement;
        for (let w = 0; w < 6 && up && up.nodeType === 1; w++) {
          if (_isCalendarGridNode(up)) root = up;
          up = up.parentElement;
        }
        // A wrapper holding the nav chrome may sit just outside the matched widget.
        if (!_findNavButton(root, "next") && !_findNavButton(root, "prev")) {
          let widen = root.parentElement;
          for (let w = 0; w < 3 && widen; w++) {
            if (_findNavButton(widen, "next") || _findNavButton(widen, "prev")) { root = widen; break; }
            widen = widen.parentElement;
          }
        }
        return root;
      }
      cur = cur.parentElement;
    }
    return null;
  }

  function _findCalendarHeader(root) {
    const preferred = root.querySelector('[role="heading"],[class*="caption"],[class*="header"],[class*="month"],[class*="label"]');
    if (preferred && _MONTH_YEAR_RE.test(safeText(preferred, 60))) return preferred;
    let all = [];
    try { all = root.querySelectorAll("*"); } catch (_e) {}
    for (let i = 0; i < all.length && i < 200; i++) {
      const node = all[i];
      if (node.children && node.children.length > 0) continue; // leaf-ish nodes only
      const txt = safeText(node, 60);
      if (txt && _MONTH_YEAR_RE.test(txt)) return node;
    }
    return null;
  }

  // Calendar cells label themselves in prose with an ordinal day — react-datepicker writes
  // aria-label="Choose Thursday, March 15th, 2007". Date.parse rejects that outright (both the
  // leading words and the "15th" suffix), so every aria-labelled day cell parsed to null, no
  // iso_date reached DateContext, and compiler/date_picker.py had no day event to collapse a
  // year/month/day run around. The replayed run then changed year and month but never picked a
  // day, and the field kept whatever date it already had.
  function _isoDateCandidates(s) {
    const raw = String(s).trim();
    const out = [raw];
    // "March 15th, 2007" -> "March 15, 2007"
    const deOrdinal = raw.replace(/(\d{1,2})(st|nd|rd|th)\b/gi, "$1");
    if (deOrdinal !== raw) out.push(deOrdinal);
    // Pull a bare date out of a sentence, ISO first, then "Month D, YYYY" / "D Month YYYY".
    const patterns = [
      /\d{4}-\d{2}-\d{2}/,
      /[A-Za-z]{3,}\s+\d{1,2},?\s+\d{4}/,
      /\d{1,2}\s+[A-Za-z]{3,},?\s+\d{4}/,
    ];
    for (const re of patterns) {
      const m = deOrdinal.match(re);
      if (m) out.push(m[0]);
    }
    return out;
  }

  function _parseDateFromString(s) {
    if (!s) return null;
    for (const candidate of _isoDateCandidates(s)) {
      const t = Date.parse(candidate);
      if (isNaN(t)) continue;
      const d = new Date(t);
      if (d.getFullYear() < 1900 || d.getFullYear() > 2200) continue;
      // Local parts, never toISOString(): a date string with no time parses to LOCAL midnight,
      // which toISOString() then converts to UTC — on any positive-offset zone (IST, CET, …)
      // that lands on the PREVIOUS day, so every recorded date was silently off by one.
      return d.getFullYear()
        + "-" + String(d.getMonth() + 1).padStart(2, "0")
        + "-" + String(d.getDate()).padStart(2, "0");
    }
    return null;
  }

  // Attribute name is reported alongside the parsed date so the compiler can tell the runtime
  // how to re-query a DIFFERENT target date later (`[data-date="<new-iso>"]`) instead of reusing
  // this recording's literal cell selector, which is only ever valid for the day it was recorded
  // on. aria-label/title values are full sentences ("Choose Sunday, September 15th, 2026") that
  // can't be regenerated without knowing the site's exact phrasing/locale, so those report attr
  // "" — the runtime falls back to locating the day by its number text instead (see
  // runtime/app/date_picker.js), which works regardless of aria phrasing or locale.
  const _CELL_DATE_ATTRS = ["data-date", "datetime", "data-day", "data-value"];

  function _cellIsoDate(cell) {
    if (!cell || !cell.getAttribute) return null;
    for (const attr of _CELL_DATE_ATTRS) {
      const iso = _parseDateFromString(cell.getAttribute(attr));
      if (iso) return { iso, attr };
    }
    for (const attr of ["aria-label", "title"]) {
      const iso = _parseDateFromString(cell.getAttribute(attr));
      if (iso) return { iso, attr: "" };
    }
    return null;
  }

  // Last-resort fallback for widgets whose day cell carries NEITHER a machine attribute NOR an
  // aria-label/title — jQuery UI's stock day cell is exactly this (`<a>15</a>` inside a
  // `<td data-month data-year>`, nothing else). Without this, such a click never gets a
  // date_context at all, so collapse_date_picker_runs (date_picker.py) never finds a "day" event
  // to collapse the run around and the whole open/nav/pick sequence compiles as ordinary,
  // unparameterized clicks. Returns { iso, attr: "" } — same shape as the aria-label fallback
  // above, so every downstream consumer (grid_only strategy, runtime's dayNumberSelector) already
  // handles it with no further changes.
  const _DAY_NUMBER_RE = /^\d{1,2}$/;
  const _OVERFLOW_CELL_RE = /disabled|outside|other-month|prev-month|next-month|adjacent/i;

  function _isOverflowOrDisabledCell(cell) {
    if (!cell) return false;
    if (cell.getAttribute && (cell.getAttribute("aria-disabled") === "true" || cell.getAttribute("aria-hidden") === "true")) {
      return true;
    }
    if (_OVERFLOW_CELL_RE.test(String(cell.className || ""))) return true;
    const parent = cell.parentElement;
    return !!(parent && _OVERFLOW_CELL_RE.test(String(parent.className || "")));
  }

  // Prefers a nearby ancestor's data-month/data-year (jQuery UI's own `<td>`) over the header
  // text: exact, and still correct for a multi-month grid where the header doesn't describe
  // every visible cell. jQuery UI's data-month is 0-indexed; when the header text is available it
  // disambiguates against that convention instead of assuming it blindly.
  function _monthYearFromAncestorAttrs(cell, headerText) {
    const headerDate = headerText ? new Date(headerText.trim()) : null;
    const headerMonth = headerDate && !isNaN(headerDate.getTime()) ? headerDate.getMonth() + 1 : null;
    let cur = cell;
    for (let depth = 0; depth < 3 && cur && cur.nodeType === 1; depth++) {
      if (cur.getAttribute) {
        const monthAttr = cur.getAttribute("data-month");
        const yearAttr = cur.getAttribute("data-year");
        if (monthAttr !== null && yearAttr !== null) {
          const year = parseInt(yearAttr, 10);
          const oneIndexed = parseInt(monthAttr, 10);
          const zeroIndexed = oneIndexed + 1;
          const month = headerMonth === oneIndexed ? oneIndexed : zeroIndexed;
          if (!isNaN(year) && month >= 1 && month <= 12) return { year, month };
        }
      }
      cur = cur.parentElement;
    }
    return null;
  }

  function _cellIsoDateFromText(cell, headerText) {
    if (!cell || _isOverflowOrDisabledCell(cell)) return null;
    const text = safeText(cell, 10).trim();
    if (!_DAY_NUMBER_RE.test(text)) return null;
    const day = parseInt(text, 10);
    if (day < 1 || day > 31) return null;

    const my = _monthYearFromAncestorAttrs(cell, headerText);
    if (my) {
      return `${my.year}-${String(my.month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    }
    if (headerText) {
      const d = new Date(headerText.trim());
      if (!isNaN(d.getTime())) {
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
      }
    }
    return null;
  }

  function _timeOptionText(el, root) {
    if (!root.contains(el)) return null;
    const txt = safeText(el, 20).trim();
    return _TIME_OPTION_RE.test(txt) ? txt : null;
  }

  function _selectorForElement(el) {
    if (!el) return "";
    return buildStableSelector(el) || buildCssPath(el);
  }

  /** The input/combobox this grid belongs to: an explicit aria-owns/aria-controls back-reference,
   * else whichever editable field last held focus before the grid appeared (tracked by the
   * focusin listener below). Returns null for inline always-visible calendars with no field. */
  function _findAnchoredField(root) {
    if (root.id) {
      try {
        const owner = document.querySelector(
          `[aria-owns="${CSS.escape(root.id)}"],[aria-controls="${CSS.escape(root.id)}"]`
        );
        if (owner) return owner;
      } catch (_e) {}
    }
    if (_lastFocusedEditableForDate && document.contains(_lastFocusedEditableForDate) && !root.contains(_lastFocusedEditableForDate)) {
      return _lastFocusedEditableForDate;
    }
    return null;
  }

  // Some calendar widgets (react-datepicker's showMonthDropdown/showYearDropdown mode — the
  // pattern its own docs recommend for far-back dates like a date of birth; MUI's and Ant
  // Design's year/decade pickers use the equivalent idiom) navigate month/year via native
  // <select> elements instead of click-through prev/next buttons. A native select's own class/
  // aria-label almost always says so directly.
  const _YEAR_SELECT_RE = /year/i;
  const _MONTH_SELECT_RE = /month/i;

  function _selectRoleFor(el) {
    if (!el || !el.tagName || el.tagName.toLowerCase() !== "select") return null;
    const haystack = [el.className || "", el.getAttribute("aria-label") || "", el.name || "", el.id || ""].join(" ");
    if (_YEAR_SELECT_RE.test(haystack)) return "year_select";
    if (_MONTH_SELECT_RE.test(haystack)) return "month_select";
    return null;
  }

  /** Only called for actionKind === "click" or "select" (see serializeTarget below) — costs
   * nothing on any other action type. Returns null unless `el` resolves cleanly to a day cell, a
   * prev/next nav button, a year/month <select>, or a time option inside a detected calendar
   * grid. */
  function buildDateContext(el) {
    const gridRoot = findCalendarRoot(el);
    if (!gridRoot) return null;

    const header = _findCalendarHeader(gridRoot);
    const prevBtn = _findNavButton(gridRoot, "prev");
    const nextBtn = _findNavButton(gridRoot, "next");
    const headerSelector = header ? _selectorForElement(header) : "";
    const headerText = header ? safeText(header, 60) : "";
    const gridSelector = _selectorForElement(gridRoot);

    // A year/month <select>'s own committed value is the one meaningful signal here — its class/
    // id-derived label_text ("August 2026", the picker's own live header text) is exactly the
    // captureAssociatedLabel garbage this whole feature exists to route around, so this must be
    // checked from the select tag/role itself, never from any nearby text.
    const selectRole = _selectRoleFor(el);
    if (selectRole) {
      const opt = el.selectedOptions && el.selectedOptions[0];
      const value = opt ? safeText(opt, 40) : String(el.value || "");
      return {
        role: selectRole,
        grid: gridSelector,
        header: headerSelector,
        header_text: headerText,
        select: _selectorForElement(el),
        value,
      };
    }

    if (prevBtn && el === prevBtn) {
      return { role: "nav", nav: "prev", grid: gridSelector, header: headerSelector, header_text: headerText };
    }
    if (nextBtn && el === nextBtn) {
      return { role: "nav", nav: "next", grid: gridSelector, header: headerSelector, header_text: headerText };
    }

    let cellDate = _cellIsoDate(el);
    if (!cellDate) {
      const iso = _cellIsoDateFromText(el, headerText);
      if (iso) cellDate = { iso, attr: "" };
    }
    if (cellDate) {
      const field = _findAnchoredField(gridRoot);
      return {
        role: "day",
        iso_date: cellDate.iso,
        cell_attr: cellDate.attr,
        grid: gridSelector,
        header: headerSelector,
        header_text: headerText,
        prev: prevBtn ? _selectorForElement(prevBtn) : "",
        next: nextBtn ? _selectorForElement(nextBtn) : "",
        cell: _selectorForElement(el),
        field: field ? _selectorForElement(field) : "",
        field_display_value: field ? readEditableValue(field) : "",
      };
    }

    const timeText = _timeOptionText(el, gridRoot);
    if (timeText) {
      return { role: "time", time: timeText, grid: gridSelector };
    }

    return null;
  }

  // Multiple-choice controls (radio/checkbox groups, native <select>, ARIA radiogroup/listbox
  // widgets): a recorded MCQ only captures the one option clicked, so the compiler has nothing to
  // build an enum input from and the runtime has nothing to match a caller's answer against — see
  // CLAUDE.md's date-picker plan for the sibling feature this mirrors. Observed only, same
  // try/catch discipline as buildDateContext: the recorder never probes for these, it just
  // describes what it saw. Requires >= 2 members for radio/checkbox groups so a lone standalone
  // checkbox ("I agree") stays an ordinary set_checkbox, never a one-option "choice".
  const _CHOICE_GROUP_MIN = 2;
  const _CHOICE_OPTIONS_MAX = 60;

  function _choiceOptionLabel(el) {
    return captureAssociatedLabel(el) || safeText(el, 120) || String(el.value || "");
  }

  /** Selector for the control that OPENS a popup listbox, or "" when the group is always visible.
   *
   * A custom dropdown's options exist in the DOM only while its menu is open, and the click that
   * opens it lands on a role-less, id-less <div> that resolveMeaningfulTarget correctly discards
   * as noise — so nothing in the recording ever reopens the menu, and the option step can never
   * be replayed. Capturing the opener alongside the options is what makes the selection
   * reproducible without recording every intermediate click.
   *
   * Preference order is the ARIA contract first (`[role=combobox][aria-controls=<listbox id>]`,
   * which any compliant widget exposes), then the nearest ancestor carrying a stable id — the
   * container that survives the menu closing (react-select's `#state`, e.g.).
   */
  function _choiceOpenerSelector(group) {
    if (!group) return "";
    try {
      const id = group.id;
      if (id) {
        const owner = document.querySelector(
          `[role="combobox"][aria-controls="${id}"],[role="combobox"][aria-owns="${id}"],` +
          `[aria-haspopup="listbox"][aria-controls="${id}"]`
        );
        // The owner must survive the menu closing to be usable at replay; an owner rendered
        // inside the popup itself is no more reachable than the options were.
        if (owner && !group.contains(owner)) return _selectorForElement(owner);
      }
      for (let n = group.parentElement; n && n !== document.body; n = n.parentElement) {
        if (n.id) return _selectorForElement(n);
      }
    } catch (_e) {}
    return "";
  }

  function _fieldsetLegendLabel(el) {
    const fs = el.closest("fieldset");
    if (!fs) return null;
    const legend = fs.querySelector("legend");
    return legend ? safeText(legend, 120) : null;
  }

  function _ariaGroupLabel(root) {
    const aria = root.getAttribute("aria-label");
    if (aria) return aria;
    const labelledBy = root.getAttribute("aria-labelledby");
    if (labelledBy) {
      const ref = document.getElementById(labelledBy);
      if (ref) return safeText(ref, 120);
    }
    return null;
  }

  function _nativeGroupMembers(el, type) {
    const name = el.getAttribute("name");
    if (!name) return [el];
    const form = el.form || document;
    let members;
    try {
      members = Array.from(form.querySelectorAll(`input[type="${type}"][name="${cssEscapeIdent(name)}"]`));
    } catch (_e) {
      members = [el];
    }
    return members.length ? members : [el];
  }

  function buildChoiceContext(el) {
    if (!el || el.nodeType !== 1) return null;
    const tag = el.tagName.toLowerCase();
    const type = inputTypeOf(el);
    const role = nodeRole(el);

    if (tag === "select") {
      const options = Array.from(el.options || []).slice(0, _CHOICE_OPTIONS_MAX).map((opt) => ({
        value: String(opt.value),
        label: safeText(opt, 120) || String(opt.value),
        selector: "",
        checked: !!opt.selected,
      }));
      if (!options.length) return null;
      return {
        kind: "select",
        multi: !!el.multiple,
        group_key: el.getAttribute("name") || el.id || "",
        group_label: captureAssociatedLabel(el) || "",
        group_selector: "",
        options,
      };
    }

    if (tag === "input" && (type === "radio" || type === "checkbox")) {
      const members = _nativeGroupMembers(el, type);
      // A single radio with no siblings is meaningless (nothing to choose between), and a lone
      // checkbox ("I agree") is an ordinary boolean, not a pick-among-options control -- both
      // stay a plain set_radio/set_checkbox step. Applies to both kinds; a radio with only one
      // member is exactly as pointless as a checkbox group of one.
      if (members.length < _CHOICE_GROUP_MIN) return null;
      const groupLabel = _fieldsetLegendLabel(el) || "";
      const fs = el.closest("fieldset");
      return {
        kind: type,
        multi: type === "checkbox",
        group_key: el.getAttribute("name") || "",
        group_label: groupLabel,
        group_selector: fs ? _selectorForElement(fs) : "",
        options: members.slice(0, _CHOICE_OPTIONS_MAX).map((m) => ({
          value: String(m.value),
          label: _choiceOptionLabel(m),
          selector: _selectorForElement(m),
          checked: !!m.checked,
        })),
      };
    }

    if (role === "radio" || role === "option") {
      const groupSelector = el.closest('[role="radiogroup"],[role="listbox"]');
      if (!groupSelector) return null;
      const kind = nodeRole(groupSelector) === "listbox" ? "aria_listbox" : "aria_radio";
      const memberRole = kind === "aria_listbox" ? "option" : "radio";
      const members = Array.from(groupSelector.querySelectorAll(`[role="${memberRole}"]`));
      if (members.length < _CHOICE_GROUP_MIN) return null;
      return {
        kind,
        multi: false,
        group_key: groupSelector.id || "",
        group_label: _ariaGroupLabel(groupSelector) || "",
        group_selector: _selectorForElement(groupSelector),
        opener_selector: _choiceOpenerSelector(groupSelector),
        options: members.slice(0, _CHOICE_OPTIONS_MAX).map((m) => ({
          value: m.getAttribute("data-value") || safeText(m, 120) || "",
          label: safeText(m, 120) || m.getAttribute("aria-label") || "",
          selector: _selectorForElement(m),
          checked: m.getAttribute("aria-checked") === "true" || m.getAttribute("aria-selected") === "true",
        })),
      };
    }

    return null;
  }

  function serializeTarget(el, actionKind, value) {
    const tag = (el.tagName && el.tagName.toLowerCase()) || "unknown";
    const id = el.id || null;
    const classes = el.classList ? Array.from(el.classList) : [];
    let innerText = safeText(el, 2000);
    const role = el.getAttribute("role") || implicitAriaRole(el);
    let aria = el.getAttribute("aria-label");
    const name = el.getAttribute("name");
    const shadowPath = shadowHostChain(el);
    // Shadow-DOM component wrappers (e.g. <sl-button>Primary</sl-button>) project their visible
    // label into the shadow-internal element via a <slot> — the internal element itself (what
    // composedPath()[0] hands us) has no innerText/aria-label of its own. Without this fallback
    // every such element records with an empty name, so the compiler's only signal left is a
    // generic shadow-internal CSS class shared by every sibling of that variant — see FIX.md.
    if (!innerText && !aria && shadowPath.length) {
      const host = el.getRootNode().host;
      // textContent, not innerText: the host's light-DOM label text only renders (and so only
      // shows up in innerText) when the shadow template actually wires up a <slot> for it — a
      // misconfigured or slot-less wrapper would otherwise still report no label despite one
      // being right there in the markup. textContent reads the light DOM directly regardless.
      innerText = String(host.textContent || "").replace(/\s+/g, " ").trim().slice(0, 2000);
      aria = host.getAttribute("aria-label");
    }
    const inputType = el.getAttribute("type") || (isEditableNode(el) && tag !== "select" ? "text" : null);
    const rect = el.getBoundingClientRect();
    const scrollX = window.scrollX || window.pageXOffset || 0;
    const scrollY = window.scrollY || window.pageYOffset || 0;
    const viewport = `${Math.round(window.innerWidth)}x${Math.round(window.innerHeight)}`;
    const scroll_position = `${Math.round(scrollX)},${Math.round(scrollY)}`;
    // Viewport-relative box so Python can crop the viewport screenshot deterministically.
    const bbox = {
      x: Math.max(0, Math.round(rect.left)),
      y: Math.max(0, Math.round(rect.top)),
      w: Math.max(0, Math.round(rect.width)),
      h: Math.max(0, Math.round(rect.height)),
    };
    const semantic = {
      normalized_text: normalizedText(el),
      role: role || tag,
      input_type: inputType,
      intent_hint: intentHint(tag, inputType, role, innerText.toLowerCase()),
    };
    const selectors = {
      css: buildCssPath(el),
      xpath: buildXPath(el),
      text_based: buildTextSelector(el),
      aria: buildAriaSelector(el),
    };
    const context = {
      parent: parentSummary(el),
      siblings: siblingSummaries(el, siblingsMax),
      index_in_parent: indexInParent(el),
      form_context: nearestForm(el),
    };
    const anchors = pickAnchors(el);
    const page = { url: location.href, title: document.title || "" };
    const before = pageFingerprint();
    // Conditional-state observation (recording-next-steps.md Priority 2): does this action's
    // target sit inside an optional interstitial (dialog / cookie-consent banner)? Observed only
    // — the recorder never probes for these, it just flags what it saw. Human-gated in Studio:
    // this never changes compiled behavior on its own (see build.py), only surfaces a suggestion.
    let branchHint = null;
    try { branchHint = buildBranchHint(el); } catch (_e) {}
    let dialogContext = null;
    try { dialogContext = buildDialogContext(el); } catch (_e) {}
    // Custom date-picker detection (only meaningful on click/select — see buildDateContext).
    let dateContext = null;
    if (actionKind === "click" || actionKind === "select") {
      try { dateContext = buildDateContext(el); } catch (_e) {}
    }
    // Multiple-choice control detection (only meaningful on the actions that can land on one —
    // see buildChoiceContext). A dateContext hit takes priority: a year/month <select> inside a
    // calendar grid is date-picker navigation, not an independent MCQ input.
    let choiceContext = null;
    if (!dateContext && _VALUE_SET_ACTIONS.indexOf(actionKind) >= 0) {
      try { choiceContext = buildChoiceContext(el); } catch (_e) {}
    }
    // Phase 2 signals (compile-time LLM input). Failures fall back to empty defaults.
    let ancestorsChain = [];
    let surroundingText = "";
    let domSigShort = "";
    try { ancestorsChain = captureAncestors(el, 24); } catch (_e) {}
    try { surroundingText = captureSurroundingText(el, 200); } catch (_e) {}
    try { domSigShort = _domSignatureHash(interactiveSignature() + "|" + _dedupSigExtra()); } catch (_e) {}
    return {
      action: {
        action: actionKind,
        timestamp: new Date().toISOString(),
        value: value == null ? null : String(value),
      },
      target: {
        tag,
        id,
        classes,
        inner_text: innerText,
        role,
        aria_label: aria,
        name,
        placeholder: el.getAttribute("placeholder") || null,
        label_text: captureAssociatedLabel(el),
        // Real accessible-name sources for non-form elements. An <img>'s name is its alt;
        // without it the compiler had nothing but captureAssociatedLabel's last-resort
        // "nearest surrounding text" walk, which names an avatar after the paragraph above it.
        alt: el.getAttribute("alt") || null,
        title: el.getAttribute("title") || null,
        attributes: collectIdentityAttrs(el),
      },
      selectors,
      context,
      semantic,
      anchors,
      shadow_path: shadowPath,
      visual_placeholder: {
        bbox,
        viewport,
        scroll_position,
      },
      page,
      optionality: branchHint ? "stochastic" : null,
      branch_hint: branchHint,
      date_context: dateContext,
      choice_context: choiceContext,
      dialog_context: dialogContext,
      // Evidence for post-condition classification at finalize (finalizeStateWithAfter) — never
      // serialized: state_probe (including the raw `el` ref) is deleted before report().
      state_probe: {
        before,
        dom_before: interactiveSignature(),
        el,
        dialog_before: !!_queryOpenDialog(),
        expanded_before: el.getAttribute ? el.getAttribute("aria-expanded") : null,
      },
      // Phase 2: compile-time signals for LLM selector generation.
      ancestors: ancestorsChain,
      surrounding_text: surroundingText,
      dom_signature_short: domSigShort,
    };
  }

  function report(payload) {
    const fn = window["__skillReport"];
    if (typeof fn === "function") {
      return fn(payload);
    }
    // Fallback for iframes where the Playwright binding isn't available
    if (window !== window.top) {
      try {
        window.parent.postMessage({ __skillBridgeRelay__: true, payload }, "*");
      } catch (_e) {}
    }
  }

  function finalizeState(payload) {
    const after = pageFingerprint();
    return finalizeStateWithAfter(payload, after);
  }

  // Click (and click-family: dblclick/right_click) is the action most likely to trigger a
  // meaningful DOM change — a dropdown, panel, or dialog appearing — but the click listener runs
  // in the CAPTURE phase, before the page's own bubble-phase handler (e.g. React's delegated
  // onClick) has even fired, let alone re-rendered. Calling finalizeState() synchronously there
  // always diffs the page against itself: dom_diff comes back empty on effectively every click.
  // Wait for the DOM to go quiet (or a max ceiling, for network-driven updates that never fully
  // settle) before capturing the "after" snapshot, mirroring the existing hover/drag_drop
  // deferral pattern below but with an actual mutation-based settle instead of a fixed frame.
  let _finalizeQueue = Promise.resolve();

  function _waitForDomSettle(quietMs, maxWaitMs) {
    return new Promise((resolve) => {
      let done = false;
      let quietTimer = null;
      let mo = null;
      function finish() {
        if (done) return;
        done = true;
        if (quietTimer) clearTimeout(quietTimer);
        clearTimeout(maxTimer);
        try { mo && mo.disconnect(); } catch (_e) {}
        resolve();
      }
      function scheduleQuiet() {
        if (quietTimer) clearTimeout(quietTimer);
        quietTimer = setTimeout(finish, quietMs);
      }
      const maxTimer = setTimeout(finish, maxWaitMs);
      try {
        mo = new MutationObserver(scheduleQuiet);
        mo.observe(document.documentElement || document, {
          childList: true,
          subtree: true,
          attributes: true,
          characterData: true,
        });
      } catch (_e) {
        mo = null;
      }
      scheduleQuiet();
    });
  }

  // Finalizes in submission order (not completion order) so two fast clicks in a row can never
  // have their state_change/dom_diff reported out of sequence.
  // Pending payloads are tracked so beforeunload can force-flush them (see below) — otherwise a
  // click that triggers fast navigation races the settle wait and the step is silently dropped.
  let _pendingClickPayloads = [];
  function finalizeStateAfterSettle(payload, quietMs, maxWaitMs) {
    _pendingClickPayloads.push(payload);
    _finalizeQueue = _finalizeQueue
      .then(() => _waitForDomSettle(quietMs, maxWaitMs))
      .then(() => {
        _pendingClickPayloads = _pendingClickPayloads.filter((p) => p !== payload);
        if (payload.__flushed) return;
        finalizeState(payload);
      })
      .catch(() => {});
    return _finalizeQueue;
  }

  function finalizeStateWithAfter(payload, after) {
    const domAfter = interactiveSignature();
    const domBefore = payload.state_probe && payload.state_probe.dom_before ? payload.state_probe.dom_before : "";
    payload.state_change = {
      before: payload.state_probe.before,
      after,
      // DOM diff: elements added/removed since the action fired
      dom_diff: _computeDomDiff(domBefore, domAfter),
    };
    // Feed the recent-appearance ring buffer (Priority 2) — a second consumer of this same
    // dom_diff, used by the *next* action's detectOptionalContainer to tell "just appeared" from
    // "was always there".
    _rememberDomDiffAdded(payload.state_change.dom_diff);
    // Post-condition distillation (recording-next-steps.md Priority 1): classify the delta we
    // already captured above into a small structured signal the compiler can turn into a
    // *specific* assertion (dialog opened / value committed / navigated) instead of a generic
    // state-change check. Plain DOM reads only — no LLM, no new capture pass.
    try {
      payload.post_condition = buildPostCondition(payload, payload.state_probe);
    } catch (_e) {
      payload.post_condition = null;
    }
    delete payload.state_probe;
    return report(payload);
  }

  function _computeDomDiff(before, after) {
    if (!before && !after) return null;
    const beforeSet = new Set((before || "").split("\n").filter(Boolean));
    const afterSet  = new Set((after || "").split("\n").filter(Boolean));
    const added   = Array.from(afterSet).filter(l => !beforeSet.has(l)).slice(0, 20);
    const removed = Array.from(beforeSet).filter(l => !afterSet.has(l)).slice(0, 20);
    if (!added.length && !removed.length) return null;
    return { added, removed };
  }

  function _queryOpenDialog() {
    try {
      return document.querySelector('[role="dialog"],[role="alertdialog"],[aria-modal="true"],dialog[open]');
    } catch (_e) {
      return null;
    }
  }

  // Action kinds whose direct effect is "the field now holds a committed value" — the enforced
  // post-condition for these is what the field reads back as, not the generic DOM delta.
  const _VALUE_SET_ACTIONS = ["type", "fill", "select", "select_option", "set_checkbox", "set_radio", "date_pick"];

  /** Stable, Playwright-usable selector for a dialog/interstitial container (id > stable attrs >
   * role > full CSS path) — feeds the compiler's selector_present assertion. */
  function buildDialogSignal(dialogEl) {
    if (!dialogEl || dialogEl.nodeType !== 1) return null;
    if (dialogEl.id) return "#" + cssEscapeIdent(dialogEl.id);
    const stable = buildStableSelector(dialogEl);
    if (stable) return stable;
    const role = dialogEl.getAttribute("role") || (dialogEl.getAttribute("aria-modal") === "true" ? "dialog" : null);
    if (role) return `[role="${role}"]`;
    return buildCssPath(dialogEl) || null;
  }

  /** Classify the already-captured before/after evidence into one small structured signal.
   * Priority order (single classified_effect per event, most specific first): navigation >
   * dialog open/close > aria-expanded flip > value commit > generic content change > none. */
  function buildPostCondition(payload, probe) {
    const actionKind = payload.action && payload.action.action;
    const el = probe && probe.el;
    const beforeUrl = (payload.page && payload.page.url) || "";
    const afterUrl = location.href;

    let classified_effect = "none";
    let value_readback = null;
    let url_delta = null;
    let dialog_signal = null;

    if (afterUrl !== beforeUrl) {
      classified_effect = "navigation";
      url_delta = { before: beforeUrl, after: afterUrl };
    } else {
      const dialogNow = _queryOpenDialog();
      const dialogWasOpen = !!(probe && probe.dialog_before);
      if (!!dialogNow !== dialogWasOpen) {
        classified_effect = dialogNow ? "dialog_opened" : "dialog_closed";
        if (dialogNow) {
          try { dialog_signal = buildDialogSignal(dialogNow); } catch (_e) {}
        }
      } else if (
        el && el.getAttribute && probe &&
        probe.expanded_before !== el.getAttribute("aria-expanded")
      ) {
        classified_effect = "expansion";
      } else if (_VALUE_SET_ACTIONS.indexOf(actionKind) >= 0) {
        classified_effect = "value_set";
        if (el) {
          try {
            value_readback = isSensitiveEditable(el) ? "{{REDACTED}}" : readEditableValue(el);
          } catch (_e) {}
        }
      } else if (payload.state_change && payload.state_change.dom_diff) {
        classified_effect = "content_change";
      }
    }

    return { classified_effect, value_readback, url_delta, dialog_signal };
  }

  // ─── Conditional-state observation (recording-next-steps.md Priority 2) ───────────────────
  // Ring buffer of recently-added interactive-element signatures (fed by P1's dom_diff.added,
  // one entry per finalized action) — lets a later action's container detection distinguish
  // "this banner just appeared" from "this banner was always on the page".
  const _RECENT_ADDED_MAX = 5;
  let _recentAddedSets = [];

  function _rememberDomDiffAdded(domDiff) {
    if (!domDiff || !domDiff.added || !domDiff.added.length) return;
    _recentAddedSets.push(new Set(domDiff.added));
    if (_recentAddedSets.length > _RECENT_ADDED_MAX) _recentAddedSets.shift();
  }

  // true = confirmed recently added; false = ring buffer had data but no overlap; null =
  // unconfirmable (empty buffer, e.g. first action of the session, or container has no
  // interactive descendants to fingerprint) — callers treat null like true (harmless to stamp).
  function _containerAppearedRecently(containerEl) {
    if (!_recentAddedSets.length) return null;
    let sig = "";
    try { sig = interactiveSignature(containerEl); } catch (_e) { return null; }
    const lines = sig.split("\n").filter(Boolean);
    if (!lines.length) return null;
    for (const set of _recentAddedSets) {
      for (const line of lines) {
        if (set.has(line)) return true;
      }
    }
    return false;
  }

  // Small, legible heuristic list — real-world consent/banner tooling id/class tokens.
  const _OPTIONAL_BANNER_TOKENS = /\b(cookie|consent|gdpr|onetrust|truste|banner)\b/i;

  function _optionalContainerStrength(node) {
    if (!node || node.nodeType !== 1 || !node.getAttribute) return null;
    const role = (node.getAttribute("role") || "").toLowerCase();
    const ariaModal = (node.getAttribute("aria-modal") || "").toLowerCase();
    if (role === "dialog" || role === "alertdialog" || ariaModal === "true") return "dialog";
    if (node.tagName === "DIALOG" && node.hasAttribute("open")) return "dialog";
    const haystack = `${node.id || ""} ${node.className || ""}`.toLowerCase();
    if (_OPTIONAL_BANNER_TOKENS.test(haystack)) return "banner";
    return null;
  }

  /** Full picture of the dialog `el` sits inside: title, message and EVERY visible button, with the
   * clicked one marked. Observation only. Lets the compiler tell a decision ("Replace / Keep both /
   * Cancel") from a dismissable popup instead of seeing just the one winning click. Null when `el`
   * is not inside a dialog-strength container (banners are deliberately not described here). */
  function buildDialogContext(el) {
    let container = null;
    let cur = el;
    for (let depth = 0; depth < 10 && cur && cur.nodeType === 1; depth++) {
      if (_optionalContainerStrength(cur) === "dialog") { container = cur; break; }
      const tag = cur.tagName ? cur.tagName.toLowerCase() : "";
      if (tag === "body" || tag === "html") break;
      cur = cur.parentElement;
    }
    if (!container) return null;
    const clean = (t) => String(t || "").replace(/\s+/g, " ").trim();
    let title = "";
    const labelledBy = container.getAttribute("aria-labelledby");
    if (labelledBy) {
      const t = document.getElementById(labelledBy.split(/\s+/)[0]);
      if (t) title = clean(t.textContent);
    }
    if (!title) title = clean(container.getAttribute("aria-label"));
    if (!title) {
      const h = container.querySelector("h1,h2,h3,h4,[role=heading]");
      if (h) title = clean(h.textContent);
    }
    const btnNodes = Array.from(container.querySelectorAll("button,[role=button],input[type=button],input[type=submit]"))
      .filter(isVisibleElement)
      .slice(0, 8);
    const buttons = btnNodes.map((b) => {
      const text = clean(b.textContent || b.value);
      return {
        tag: b.tagName.toLowerCase(),
        role: b.getAttribute("role") || "button",
        name: clean(b.getAttribute("aria-label")) || text,
        text: text.slice(0, 80),
        testid: b.getAttribute("data-testid") || "",
        id: b.id || "",
        chosen: b === el || b.contains(el),
      };
    });
    if (!buttons.length) return null;
    const message = clean(container.textContent).slice(0, 300);
    const hasFields = !!container.querySelector(
      "input:not([type=button]):not([type=submit]):not([type=hidden]):not([type=checkbox]):not([type=radio]),textarea,select,[contenteditable=true]"
    );
    return {
      kind: "dom",
      container_signal: buildDialogSignal(container) || "",
      role: container.getAttribute("role") || (container.tagName === "DIALOG" ? "dialog" : ""),
      title: title.slice(0, 120),
      message,
      has_fields: hasFields,
      buttons,
    };
  }

  /** Walk up from el (bounded) looking for a dialog/consent-banner container. Returns
   * {containerEl, container_signal, strength} or null. Observation only — never probes,
   * never clicks anything; just reports what the human's own action happened to be inside of. */
  function detectOptionalContainer(el) {
    let cur = el;
    for (let depth = 0; depth < 10 && cur; depth++) {
      const strength = _optionalContainerStrength(cur);
      if (strength) {
        return { containerEl: cur, container_signal: buildDialogSignal(cur), strength };
      }
      const tag = cur.tagName ? cur.tagName.toLowerCase() : "";
      if (tag === "body" || tag === "html") break;
      cur = cur.parentElement;
    }
    return null;
  }

  /** {kind: "try_dismiss", container_signal} when the target sits inside an optional
   * interstitial, else null. `role=dialog`/`aria-modal` is high-confidence on its own; a plain
   * id/class banner-token match is confirmed against the recent-appearance ring buffer when
   * there's data to check against — unconfirmable or confirmed cases still stamp (false
   * positives are harmless: an unconfirmed hint compiles to a required step, exactly as today,
   * until a human confirms it in Human Edit — see build.py). */
  function buildBranchHint(el) {
    const found = detectOptionalContainer(el);
    if (!found) return null;
    if (found.strength === "banner" && _containerAppearedRecently(found.containerEl) === false) {
      return null;
    }
    return { kind: "try_dismiss", container_signal: found.container_signal };
  }

  let inputTimer = null;
  let lastInputEl = null;
  const lastEditableValueByElement = new WeakMap();
  // Custom date-picker detection: the field that last held focus, used to identify which
  // input/combobox a custom calendar grid belongs to when no aria-owns/aria-controls link
  // exists (see buildDateContext below).
  let _lastFocusedEditableForDate = null;

  function emitEditableChange(el, force) {
    const target = resolveEditableTarget(el);
    if (!target) return;
    const compareValue = editableComparisonValue(target);
    const previous = lastEditableValueByElement.get(target);
    if (!force && previous === compareValue) return;
    lastEditableValueByElement.set(target, compareValue);
    const value = isSensitiveEditable(target) ? "{{REDACTED}}" : readEditableValue(target);
    const p = serializeTarget(target, "type", value);
    p.action.value = value;
    finalizeState(p);
  }

  function scheduleInputFlush(el, force) {
    const target = resolveEditableTarget(el);
    if (!target) return;
    lastInputEl = target;
    if (inputTimer) clearTimeout(inputTimer);
    inputTimer = setTimeout(() => {
      inputTimer = null;
      const target = lastInputEl;
      if (!target) return;
      emitEditableChange(target, !!force);
    }, inputDebounceMs);
  }

  function rememberEditableBaseline(el) {
    const target = resolveEditableTarget(el);
    if (!target) return;
    lastEditableValueByElement.set(target, editableComparisonValue(target));
  }

  function deepActiveElement(root) {
    let active = (root || document).activeElement;
    for (let depth = 0; depth < 8 && active && active.shadowRoot && active.shadowRoot.activeElement; depth++) {
      active = active.shadowRoot.activeElement;
    }
    return active;
  }

  function activeEditableTarget() {
    return resolveEditableTarget(deepActiveElement(document));
  }

  // "Capture a value": the person pressed the button in Studio, so the NEXT click reads the element
  // instead of acting on it. It is reported as an `extract` event whose target is serialized exactly like
  // a click's (so it is found the same way at replay); the page never sees the click. One shot: capture
  // mode turns itself off, and Studio can switch it off early with window.__conxaCapture(false).
  let _captureNext = false;
  window.__conxaCapture = function (on) {
    _captureNext = !!on;
    try { document.documentElement.style.cursor = _captureNext ? "crosshair" : ""; } catch (_e) {}
    return _captureNext;
  };
  function _captureSource(el) {
    const tag = (el.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" ? "value" : "text";
  }

  onDoc(
    "click",
    (ev) => {
      trace("event", { t: "click" });
      let el = eventTargetFromPath(ev);
      if (!el || el.nodeType !== 1) return;
      if (_captureNext) {
        ev.preventDefault();
        ev.stopPropagation();
        if (typeof ev.stopImmediatePropagation === "function") ev.stopImmediatePropagation();
        const picked = resolveMeaningfulTarget(el) || el;
        window.__conxaCapture(false);
        const p = serializeTarget(picked, "extract", null);
        p.extract = { source: _captureSource(picked) };
        finalizeState(p);
        return;
      }
      const resolved = resolveMeaningfulTarget(el);
      if (resolved) {
        flushPendingHoverBeforeClick(resolved);
        // ARIA radiogroup/listbox widgets (a <div role="radio">/[role="option"]) never fire a
        // native "change" event the way input[type=radio]/<select> do -- a click IS the whole
        // selection gesture. Recording it as a plain "click" would lose which option was picked
        // (see buildChoiceContext); record it as the same set_radio/select_option action a native
        // control would emit so the rest of the pipeline (dedupe, compiler, runtime) treats it
        // identically to any other multiple-choice selection.
        let ariaChoiceAction = null;
        try {
          const ctx = buildChoiceContext(resolved);
          if (ctx && (ctx.kind === "aria_radio" || ctx.kind === "aria_listbox")) {
            const picked = ctx.options.find((o) => o.selector === _selectorForElement(resolved));
            ariaChoiceAction = { action: ctx.kind === "aria_listbox" ? "select_option" : "set_radio", value: picked ? picked.value : "" };
          }
        } catch (_e) {}
        const p = ariaChoiceAction
          ? serializeTarget(resolved, ariaChoiceAction.action, ariaChoiceAction.value)
          : serializeTarget(resolved, "click", null);
        finalizeStateAfterSettle(p, clickSettleQuietMs, clickSettleMaxMs);
        return;
      }
    },
    true
  );

  onDoc(
    "change",
    (ev) => {
      const el = eventTargetFromPath(ev);
      if (!el || el.nodeType !== 1) return;
      const editable = resolveEditableTarget(el);
      const target = editable || el;
      const tag = target.tagName.toLowerCase();
      if (tag === "select") {
        const val = "value" in target ? target.value : null;
        const p = serializeTarget(target, "select", val);
        finalizeState(p);
        return;
      }
      if (tag === "input") {
        const inputType = (target.getAttribute("type") || "text").toLowerCase();
        if (inputType === "file") {
          const files = Array.from(target.files || []).map(function(f) {
            return { name: f.name, size: f.size, type: f.type };
          });
          const p = serializeTarget(target, "upload_intent", JSON.stringify(files));
          finalizeState(p);
          return;
        }
        if (inputType === "checkbox") {
          const p = serializeTarget(target, "set_checkbox", target.checked ? "true" : "false");
          finalizeState(p);
          return;
        }
        if (inputType === "radio") {
          const p = serializeTarget(target, "set_radio", target.value);
          finalizeState(p);
          return;
        }
        if (["date", "datetime-local", "time", "month", "week"].indexOf(inputType) >= 0) {
          const p = serializeTarget(target, "date_pick", target.value);
          finalizeState(p);
          return;
        }
        scheduleInputFlush(target);
        return;
      }
      if (editable) {
        scheduleInputFlush(editable);
      }
    },
    true
  );

  onDoc(
    "beforeinput",
    (ev) => {
      trace("event", { t: "beforeinput" });
      const editable = resolveEditableTarget(eventTargetFromPath(ev)) || activeEditableTarget();
      if (editable) scheduleInputFlush(editable);
    },
    true
  );

  onDoc(
    "input",
    (ev) => {
      trace("event", { t: "input" });
      const editable = resolveEditableTarget(eventTargetFromPath(ev));
      if (editable) scheduleInputFlush(editable);
    },
    true
  );

  onDoc(
    "keyup",
    () => {
      const editable = activeEditableTarget();
      if (editable) scheduleInputFlush(editable);
    },
    true
  );

  onDoc(
    "focusin",
    (ev) => {
      trace("event", { t: "focusin" });
      const focusEl = eventTargetFromPath(ev);
      rememberEditableBaseline(focusEl);
      const editable = resolveEditableTarget(focusEl);
      if (editable) _lastFocusedEditableForDate = editable;
    },
    true
  );

  onDoc(
    "focusout",
    (ev) => {
      trace("event", { t: "focusout" });
      const editable = resolveEditableTarget(eventTargetFromPath(ev));
      if (editable) emitEditableChange(editable, false);
    },
    true
  );

  let scrollTimer = null;
  window.addEventListener(
    "scroll",
    () => {
      if (scrollTimer) clearTimeout(scrollTimer);
      scrollTimer = setTimeout(() => {
        scrollTimer = null;
        const el = document.documentElement;
        const p = serializeTarget(el, "scroll", null);
        p.visual_placeholder.bbox = { x: 0, y: 0, w: 0, h: 0 };
        finalizeState(p);
      }, scrollDebounceMs);
    },
    { passive: true }
  );

  onDoc(
    "dblclick",
    (ev) => {
      let el = eventTargetFromPath(ev);
      if (!el || el.nodeType !== 1) return;
      const resolved = resolveMeaningfulTarget(el);
      if (!resolved) return;
      const p = serializeTarget(resolved, "dblclick", null);
      finalizeStateAfterSettle(p, clickSettleQuietMs, clickSettleMaxMs);
    },
    true
  );

  onDoc(
    "contextmenu",
    (ev) => {
      let el = eventTargetFromPath(ev);
      if (!el || el.nodeType !== 1) return;
      const resolved = resolveMeaningfulTarget(el) || el;
      const p = serializeTarget(resolved, "right_click", null);
      finalizeStateAfterSettle(p, clickSettleQuietMs, clickSettleMaxMs);
    },
    true
  );

  // Smart hover: record only when hovering a candidate reveals/enables actionable UI.
  // Opt-in per recording (see RecordWorkflowDialog's hover checkbox) — hover signals are
  // noisy enough to need heavy human review, so most recordings should never emit them.
  const hoverCaptureEnabled = CAP.hover_capture_enabled === true;
  const hoverDwellMs = Number(CAP.hover_dwell_ms) > 0 ? Number(CAP.hover_dwell_ms) : 400;
  const hoverActionableLimit = Number(CAP.hover_actionable_limit) > 0 ? Number(CAP.hover_actionable_limit) : 160;
  const hoverLocalLimit = Number(CAP.hover_local_limit) > 0 ? Number(CAP.hover_local_limit) : 60;
  const hoverOverlayLimit = Number(CAP.hover_overlay_limit) > 0 ? Number(CAP.hover_overlay_limit) : 80;
  const hoverActionableSelector = [
    "a[href]",
    "button",
    "input:not([type='hidden'])",
    "textarea",
    "select",
    "summary",
    "[role='button']",
    "[role='link']",
    "[role='menuitem']",
    "[role='menuitemcheckbox']",
    "[role='menuitemradio']",
    "[role='option']",
    "[role='tab']",
    "[role='treeitem']",
    "[role='combobox']",
    "[role='checkbox']",
    "[role='radio']",
    "[role='switch']",
    "[aria-haspopup]",
    "[aria-expanded]",
    "[onclick]",
    "[tabindex]:not([tabindex='-1'])",
  ].join(",");
  const hoverOverlaySelector = [
    "[role='menu']",
    "[role='menubar']",
    "[role='listbox']",
    "[role='dialog']",
    "[role='tooltip']",
    "[role='tree']",
    "[role='navigation']",
    "[aria-modal='true']",
    "[popover]",
    "aside",
    "details[open]",
  ].join(",");
  let hoverTimer = null;
  let pendingHover = null;
  let lastStableHoverSnapshot = null;
  let hoverBaselineTimer = null;

  function elementClassString(el) {
    if (!el) return "";
    if (typeof el.className === "string") return el.className;
    if (el.classList && el.classList.length) return Array.from(el.classList).join(" ");
    return "";
  }

  function elementTokenString(el) {
    if (!el || !el.getAttribute) return "";
    return [
      el.id || "",
      elementClassString(el),
      el.getAttribute("data-testid") || el.getAttribute("data-test-id") || "",
      el.getAttribute("data-test") || "",
      el.getAttribute("aria-label") || "",
      el.getAttribute("title") || "",
    ].join(" ");
  }

  function hasToken(el, pattern) {
    return pattern.test(elementTokenString(el));
  }

  // Rendered = the page has decided to show this element (display/visibility/opacity/size),
  // independent of where the viewport currently sits. This is what a hover reveal actually
  // changes: `display: none → block`. Scrolling does not change it.
  function isRenderedElement(el) {
    if (!el || el.nodeType !== 1 || !el.isConnected) return false;
    const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
    if (style) {
      if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse") return false;
      if (Number(style.opacity) === 0) return false;
    }
    const rects = el.getClientRects ? el.getClientRects() : [];
    if (!rects || !rects.length) return false;
    const rect = el.getBoundingClientRect();
    return rect.width >= 1 && rect.height >= 1;
  }

  // Rendered AND currently on screen. Correct for "is this element worth recording", wrong for
  // the hover reveal diff — see collectVisibleMatches.
  function isVisibleElement(el) {
    if (!isRenderedElement(el)) return false;
    const rect = el.getBoundingClientRect();
    const vw = window.innerWidth || document.documentElement.clientWidth || 0;
    const vh = window.innerHeight || document.documentElement.clientHeight || 0;
    return rect.bottom >= 0 && rect.right >= 0 && rect.top <= vh && rect.left <= vw;
  }

  function isSignatureActionableNode(el) {
    if (!el || el.nodeType !== 1) return false;
    const tag = el.tagName.toLowerCase();
    if (["a", "button", "input", "textarea", "select", "summary"].indexOf(tag) >= 0) return true;
    const r = nodeRole(el);
    if (
      [
        "button",
        "link",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "option",
        "tab",
        "treeitem",
        "combobox",
        "checkbox",
        "radio",
        "switch",
      ].indexOf(r) >= 0
    ) {
      return true;
    }
    if (el.hasAttribute("aria-haspopup") || el.hasAttribute("aria-expanded")) return true;
    if (el.hasAttribute("onclick")) return true;
    const tabIndex = el.getAttribute("tabindex");
    return tabIndex !== null && tabIndex !== "-1";
  }

  function isOverlayLikeNode(el) {
    if (!el || el.nodeType !== 1) return false;
    const tag = el.tagName.toLowerCase();
    const r = nodeRole(el);
    if (["menu", "menubar", "listbox", "dialog", "tooltip", "tree", "navigation"].indexOf(r) >= 0) return true;
    if (tag === "aside" || tag === "details") return true;
    if (el.hasAttribute("popover") || el.getAttribute("aria-modal") === "true") return true;
    return hasToken(el, /\b(menu|menubar|submenu|dropdown|drop-down|popover|flyout|drawer|sidebar|side-nav|sidenav|overlay|mega-menu)\b/i);
  }

  function hoverTargetKey(el) {
    if (!el || el.nodeType !== 1) return "";
    return [
      el.tagName.toLowerCase(),
      el.id || "",
      nodeRole(el),
      el.getAttribute("aria-label") || "",
      el.getAttribute("name") || "",
      safeText(el, 80),
      buildCssPath(el),
    ].join("|");
  }

  function stableElementSignature(el) {
    if (!el || el.nodeType !== 1) return "";
    return [
      el.tagName.toLowerCase(),
      el.id ? "#" + el.id : "",
      nodeRole(el),
      el.getAttribute("aria-label") || "",
      el.getAttribute("name") || "",
      el.getAttribute("href") || "",
      safeText(el, 60),
      buildCssPath(el),
    ].join("|");
  }

  // Hover-signature collector. Deliberately uses isRenderedElement, NOT isVisibleElement: the
  // signature is diffed before/after a hover to decide whether the hover revealed anything, and
  // a viewport-clipped test makes that diff a function of SCROLL POSITION. Scrolling down brings
  // dozens of actionables on screen, they read as "newly revealed", and the next element the
  // mouse rests on gets recorded as a hover step that reveals nothing. A real reveal toggles
  // display/visibility, which isRenderedElement sees and scrolling cannot fake.
  function collectVisibleMatches(root, selector, predicate, limit) {
    const out = [];
    const seen = new WeakSet();
    const push = (el) => {
      if (!el || el.nodeType !== 1 || seen.has(el) || out.length >= limit) return;
      seen.add(el);
      if (isRenderedElement(el) && (!predicate || predicate(el))) out.push(el);
    };
    const scan = (scanRoot) => {
      if (!scanRoot || out.length >= limit) return;
      if (scanRoot.nodeType === 1 && scanRoot.matches) {
        try {
          if (scanRoot.matches(selector)) push(scanRoot);
        } catch (_err) {
          return;
        }
      }
      if (scanRoot.querySelectorAll) {
        let matches = [];
        try {
          matches = Array.from(scanRoot.querySelectorAll(selector));
        } catch (_err) {
          matches = [];
        }
        for (const el of matches) {
          push(el);
          if (out.length >= limit) break;
        }
        for (const el of matches) {
          if (out.length >= limit) break;
          if (el.shadowRoot) scan(el.shadowRoot);
        }
      }
    };
    scan(root || document);
    return out;
  }

  function signatureLines(elements) {
    const lines = [];
    const seen = new Set();
    for (const el of elements) {
      const sig = stableElementSignature(el);
      if (!sig || seen.has(sig)) continue;
      seen.add(sig);
      lines.push(sig);
    }
    return lines.sort();
  }

  function lineSet(snapshotValue) {
    if (!snapshotValue) return new Set();
    return new Set(String(snapshotValue).split("\n").filter(Boolean));
  }

  function addedLines(beforeValue, afterValue) {
    const before = lineSet(beforeValue);
    return Array.from(lineSet(afterValue)).filter((line) => !before.has(line));
  }

  function controlledHoverRoots(target) {
    const roots = [];
    if (!target || !target.getAttribute) return roots;
    const raw = target.getAttribute("aria-controls") || target.getAttribute("aria-owns") || "";
    for (const id of raw.split(/\s+/).filter(Boolean)) {
      const found = document.getElementById(id);
      if (found) roots.push(found);
    }
    return roots;
  }

  function adjacentHoverRoots(target) {
    const roots = [];
    if (!target || target.nodeType !== 1) return roots;
    roots.push(target);
    for (const controlled of controlledHoverRoots(target)) roots.push(controlled);
    const parent = target.parentElement;
    if (parent) roots.push(parent);
    const grand = parent && parent.parentElement;
    if (grand && (isOverlayLikeNode(grand) || isNavigationRegion(grand))) roots.push(grand);
    if (target.nextElementSibling) roots.push(target.nextElementSibling);
    if (target.previousElementSibling) roots.push(target.previousElementSibling);
    return roots;
  }

  function localActionableSignature(target) {
    if (!target || target.nodeType !== 1) return "";
    const lines = [];
    const seen = new Set();
    for (const root of adjacentHoverRoots(target)) {
      const actionables = collectVisibleMatches(root, hoverActionableSelector, isSignatureActionableNode, hoverLocalLimit);
      for (const line of signatureLines(actionables)) {
        if (seen.has(line)) continue;
        seen.add(line);
        lines.push(line);
      }
      if (lines.length >= hoverLocalLimit) break;
    }
    return lines.sort().slice(0, hoverLocalLimit).join("\n");
  }

  function localOverlaySignature(target) {
    if (!target || target.nodeType !== 1) return "";
    const lines = [];
    const seen = new Set();
    for (const root of adjacentHoverRoots(target)) {
      const overlays = collectVisibleMatches(root, hoverOverlaySelector, isOverlayLikeNode, hoverLocalLimit);
      for (const line of signatureLines(overlays)) {
        if (seen.has(line)) continue;
        seen.add(line);
        lines.push(line);
      }
      if (lines.length >= hoverLocalLimit) break;
    }
    return lines.sort().slice(0, hoverLocalLimit).join("\n");
  }

  function pageActionableSignature() {
    return signatureLines(
      collectVisibleMatches(document, hoverActionableSelector, isSignatureActionableNode, hoverActionableLimit)
    ).join("\n");
  }

  function overlaySignature() {
    const overlays = collectVisibleMatches(document, hoverOverlaySelector, isOverlayLikeNode, hoverOverlayLimit);
    return signatureLines(overlays).join("\n");
  }

  function captureHoverSnapshot(target) {
    return {
      target_key: hoverTargetKey(target),
      aria_expanded: target && target.getAttribute ? target.getAttribute("aria-expanded") || "" : "",
      actionables: pageActionableSignature(),
      overlays: overlaySignature(),
      local_actionables: localActionableSignature(target),
      local_overlays: localOverlaySignature(target),
    };
  }

  function snapshotHasAddedLocalOrGlobal(before, after) {
    return (
      addedLines(before && before.actionables, after && after.actionables).length > 0 ||
      addedLines(before && before.local_actionables, after && after.local_actionables).length > 0 ||
      addedLines(before && before.local_overlays, after && after.local_overlays).length > 0
    );
  }

  function stableSnapshotAddedNearTarget(stableBefore, after, field, localField) {
    if (!stableBefore) return false;
    const added = addedLines(stableBefore[field], after[field]);
    if (!added.length) return false;
    const localAfter = lineSet(after[localField]);
    return added.some((line) => localAfter.has(line));
  }

  function hasMeaningfulHoverChange(before, after, stableBefore) {
    if (!before || !after) return false;
    if (before.aria_expanded !== after.aria_expanded) return true;
    if (addedLines(before.overlays, after.overlays).length > 0) return true;
    if (snapshotHasAddedLocalOrGlobal(before, after)) return true;
    if (stableBefore) {
      if (stableSnapshotAddedNearTarget(stableBefore, after, "actionables", "local_actionables")) return true;
      if (stableSnapshotAddedNearTarget(stableBefore, after, "overlays", "local_overlays")) return true;
    }
    return false;
  }

  function isNavigationRegion(el) {
    let cur = el;
    for (let depth = 0; depth < 8 && cur && cur.nodeType === 1; depth++) {
      const tag = cur.tagName.toLowerCase();
      const r = nodeRole(cur);
      if (["nav", "aside"].indexOf(tag) >= 0) return true;
      if (["navigation", "menu", "menubar", "tablist", "tree", "toolbar"].indexOf(r) >= 0) return true;
      if (hasToken(cur, /\b(nav|navigation|menu|menubar|sidebar|side-nav|sidenav|drawer|tabs?|tree|toolbar)\b/i)) return true;
      if (tag === "body" || tag === "html") break;
      cur = cur.parentElement || (cur.getRootNode && cur.getRootNode().host) || null;
    }
    return false;
  }

  const HOVER_LEAF_EXCLUDE_TAGS = ["script", "style", "head", "meta", "link", "title", "template", "noscript", "br", "wbr"];

  // A real CSS `:hover` rule (e.g. the-internet.herokuapp.com/hovers' `.figure:hover
  // .figcaption { display: block }`) is applied by the browser as part of hit-testing,
  // before any JS mouseover handler — even a capture-phase one — ever runs. So by the time
  // isHoverCandidateNode() can inspect the DOM, a genuinely CSS-revealed sibling already
  // looks visible; checking "is a sibling hidden right now" can never catch this case. A
  // leaf node (no element children) is instead used as the signal: it's cheap, doesn't
  // depend on timing, and matches the common shape of a real hover-reveal trigger (an
  // avatar/icon/thumbnail) without treating every layout wrapper the mouse passes over as
  // a candidate. The before/after DOM-signature diff in hasMeaningfulHoverChange() (against
  // the pre-hover stable baseline) is what actually decides whether the hover mattered.
  function isHoverFallbackLeaf(el) {
    if (!el || el.nodeType !== 1) return false;
    if (el.children && el.children.length > 0) return false;
    return HOVER_LEAF_EXCLUDE_TAGS.indexOf(el.tagName.toLowerCase()) < 0;
  }

  function isHoverCandidateNode(el) {
    if (!el || el.nodeType !== 1 || isEditableNode(el)) return false;
    const tag = el.tagName.toLowerCase();
    const r = nodeRole(el);
    if (["a", "button", "summary"].indexOf(tag) >= 0) return true;
    if (["button", "link", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "treeitem", "option"].indexOf(r) >= 0) {
      return true;
    }
    if (el.hasAttribute("aria-haspopup") || el.hasAttribute("aria-expanded") || el.hasAttribute("aria-controls")) return true;
    if (el.title || el.hasAttribute("data-tooltip") || el.hasAttribute("data-tip") || el.hasAttribute("data-popover")) return true;
    if (hasToken(el, /\b(menu|submenu|dropdown|popover|flyout|drawer|sidebar|side-nav|sidenav|nav-item|tab|toggle)\b/i)) return true;
    if (isNavigationRegion(el) && !!safeText(el, 40)) return true;
    // No semantic hint at all (e.g. a plain <img> revealing a sibling via bare CSS :hover,
    // like the-internet.herokuapp.com/hovers) — fall back to "is this a leaf visual node";
    // the dwell-timer + before/after diff downstream is what actually decides whether the
    // hover mattered, this only decides whether it's worth checking at all.
    return isHoverFallbackLeaf(el);
  }

  function resolveHoverCandidate(el) {
    if (!el || el.nodeType !== 1) return null;
    const meaningful = resolveMeaningfulTarget(el);
    if (meaningful && !isEditableNode(meaningful) && isHoverCandidateNode(meaningful)) return meaningful;
    let cur = el;
    for (let depth = 0; depth < 10 && cur && cur.nodeType === 1; depth++) {
      if (isHoverCandidateNode(cur)) return cur;
      const tag = cur.tagName ? cur.tagName.toLowerCase() : "";
      if (tag === "body" || tag === "html") break;
      cur = cur.parentElement || (cur.getRootNode && cur.getRootNode().host) || null;
    }
    return null;
  }

  function scheduleHoverBaselineRefresh(delay) {
    if (hoverBaselineTimer) clearTimeout(hoverBaselineTimer);
    hoverBaselineTimer = setTimeout(() => {
      hoverBaselineTimer = null;
      if (!pendingHover) lastStableHoverSnapshot = captureHoverSnapshot(null);
    }, delay == null ? 80 : delay);
  }

  function clearHoverTimer() {
    if (hoverTimer) {
      clearTimeout(hoverTimer);
      hoverTimer = null;
    }
  }

  function shouldRecordHoverTarget(target) {
    if (!target || !target.isConnected || !isVisibleElement(target)) return false;
    const rect = target.getBoundingClientRect();
    if (!rect || rect.width < 2 || rect.height < 2) return false;
    const role = (target.getAttribute("role") || "").toLowerCase();
    const text = safeText(target, 80).toLowerCase();
    if ((role === "status" || role === "progressbar") && /loading|spinner|please wait/.test(text)) return false;
    return true;
  }

  function emitPendingHover(reason) {
    const candidate = pendingHover;
    if (!candidate || candidate.emitted) return false;
    clearHoverTimer();
    const after = captureHoverSnapshot(candidate.target);
    const changed = hasMeaningfulHoverChange(candidate.before, after, candidate.stableBefore);
    candidate.emitted = true;
    pendingHover = null;
    lastStableHoverSnapshot = after;
    if (!changed) {
      scheduleHoverBaselineRefresh(80);
      return false;
    }
    if (!shouldRecordHoverTarget(candidate.target)) {
      scheduleHoverBaselineRefresh(80);
      return false;
    }
    const p = serializeTarget(candidate.target, "hover", null);
    if (reason === "before_click" || reason === "candidate_switch") {
      finalizeState(p);
    } else {
      requestAnimationFrame(() => finalizeState(p));
    }
    return true;
  }

  function startHoverCandidate(candidate) {
    if (!candidate || candidate.nodeType !== 1) return;
    if (pendingHover && pendingHover.target === candidate) return;
    if (pendingHover) emitPendingHover("candidate_switch");
    clearHoverTimer();
    pendingHover = {
      target: candidate,
      before: captureHoverSnapshot(candidate),
      stableBefore: lastStableHoverSnapshot,
      emitted: false,
      startedAt: Date.now(),
    };
    hoverTimer = setTimeout(() => emitPendingHover("dwell"), hoverDwellMs);
  }

  function flushPendingHoverBeforeClick(clickTarget) {
    if (!pendingHover || pendingHover.emitted) return;
    if (pendingHover.target === clickTarget) return;
    emitPendingHover("before_click");
  }

  // The stable baseline is the reference hasMeaningfulHoverChange() diffs against for CSS-only
  // reveals (where `before` is already contaminated — see isHoverFallbackLeaf's comment). It
  // therefore has to describe the SETTLED page.
  //
  // This script is injected at document_start, so a setTimeout(…, 0) here runs while the
  // document is still parsing and captures an essentially EMPTY page. Every actionable the page
  // then renders counts as "added" against it, so the first hover after any page load — on any
  // leaf element the mouse happens to rest on for 400 ms while travelling to the real target —
  // looked like it had revealed the entire page and was recorded as a step. That is where the
  // spurious `hover` steps on static headings came from.
  //
  // Two mechanisms, because neither covers the other's case:
  //
  // 1. Capture at DOMContentLoaded (or now, if the document is already parsed). Deterministic
  //    for a server-rendered page — the reference exists before the mouse can reach anything,
  //    with no dependence on mutation timing.
  // 2. Re-capture whenever nodes are added or removed and the mouse is not mid-hover, for an
  //    app that renders client-side and is still blank at DOMContentLoaded. `pendingHover` is
  //    what makes this safe — scheduleHoverBaselineRefresh() skips the capture while a hover is
  //    being measured, so a reveal can never overwrite the reference it is about to be compared
  //    against. childList only, deliberately: a class/style-driven reveal is an ATTRIBUTE
  //    change, and that is precisely the mutation whose baseline must stay put. Node insertions
  //    (page render, lazy content, route change) are what needs to be absorbed.
  //
  // Nothing captures a baseline while the document is still parsing: a hover that beats
  // DOMContentLoaded leaves it null, which hasMeaningfulHoverChange already handles by skipping
  // the stable comparison. A missing reference is safe; a blank one is not.
  function captureStableHoverBaseline() {
    if (!pendingHover) lastStableHoverSnapshot = captureHoverSnapshot(null);
  }
  if (hoverCaptureEnabled) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", captureStableHoverBaseline, { once: true, capture: true });
    } else {
      setTimeout(captureStableHoverBaseline, 0);
    }
    try {
      new MutationObserver(() => scheduleHoverBaselineRefresh(80))
        .observe(document.documentElement || document, { childList: true, subtree: true });
    } catch (_e) { /* no observer — DOMContentLoaded + the mouseout refresh paths still run */ }

    onDoc(
      "mouseover",
      (ev) => {
        const el = eventTargetFromPath(ev);
        if (!el) return;
        const candidate = resolveHoverCandidate(el);
        if (!candidate) {
          if (pendingHover) emitPendingHover("candidate_switch");
          scheduleHoverBaselineRefresh(80);
          return;
        }
        const related = nodeAsElement(ev.relatedTarget);
        if (related && candidate.contains && candidate.contains(related)) {
          return;
        }
        startHoverCandidate(candidate);
      },
      { capture: true, passive: true }
    );
    onDoc("mouseout", function(ev) {
      if (!pendingHover || pendingHover.emitted) {
        scheduleHoverBaselineRefresh(80);
        return;
      }
      const to = nodeAsElement(ev.relatedTarget);
      if (to && pendingHover.target && pendingHover.target.contains && pendingHover.target.contains(to)) return;
      setTimeout(() => {
        if (!pendingHover || pendingHover.emitted) return;
        emitPendingHover("mouseout");
      }, Math.min(120, hoverDwellMs));
    }, { capture: true, passive: true });
  }

  // Drag / drop — capture source selector on dragstart, emit combined event on drop
  let _dragSrcSelectors = null;
  onDoc(
    "dragstart",
    (ev) => {
      const el = eventTargetFromPath(ev);
      if (!el) return;
      _dragSrcSelectors = { css: buildCssPath(el), xpath: buildXPath(el) };
    },
    true
  );
  onDoc(
    "drop",
    (ev) => {
      const dst = eventTargetFromPath(ev);
      if (!dst || !_dragSrcSelectors) return;
      const val = JSON.stringify({
        src_css:   _dragSrcSelectors.css,
        src_xpath: _dragSrcSelectors.xpath,
        dst_css:   buildCssPath(dst),
        dst_xpath: buildXPath(dst),
      });
      const p = serializeTarget(dst, "drag_drop", val);
      _dragSrcSelectors = null;
      requestAnimationFrame(() => finalizeState(p));
    },
    true
  );

  // Keyboard shortcuts — modifier combos and common non-printable keys
  var _KEY_ALLOWLIST = { Tab: 1, Enter: 1, Escape: 1, ArrowUp: 1, ArrowDown: 1, ArrowLeft: 1, ArrowRight: 1 };
  onDoc(
    "keydown",
    (ev) => {
      const hasModifier = ev.ctrlKey || ev.metaKey || ev.altKey;
      if (!hasModifier && !_KEY_ALLOWLIST[ev.key]) return;
      if (ev.key === "Control" || ev.key === "Meta" || ev.key === "Alt" || ev.key === "Shift") return;
      const val = JSON.stringify({
        key: ev.key,
        code: ev.code,
        modifiers: { ctrl: ev.ctrlKey, shift: ev.shiftKey, alt: ev.altKey, meta: ev.metaKey },
      });
      const el = document.activeElement || document.body;
      const p = serializeTarget(el, "keyboard_shortcut", val);
      requestAnimationFrame(() => finalizeState(p));
    },
    true
  );

  // Initial installation — must run after all onDoc(...) registrations above.
  installDocumentListeners();

  // Flush pending typed input and hover before the frame unloads (e.g., form submit in iframe).
  // Also reset the window flag so the pump loop re-injects the bridge after document.open()
  // (HubSpot micro-frontend pattern: window object persists but document is replaced).
  window.addEventListener("beforeunload", function () {
    window.__SKILL_BRIDGE_V1__ = false;
    if (inputTimer) {
      clearTimeout(inputTimer);
      inputTimer = null;
      if (lastInputEl) emitEditableChange(lastInputEl, true);
    }
    if (hoverTimer) {
      clearTimeout(hoverTimer);
      hoverTimer = null;
    }
    if (_pendingClickPayloads.length) {
      const toFlush = _pendingClickPayloads;
      _pendingClickPayloads = [];
      toFlush.forEach((p) => {
        p.__flushed = true;
        finalizeState(p);
      });
    }
  }, true);
})();
