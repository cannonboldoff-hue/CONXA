from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Browser, Page, sync_playwright

import conxa_compile.recorder as _recorder

BRIDGE_JS = (Path(_recorder.__file__).parent / "bridge.js").read_text(encoding="utf-8")


def _existing_chromium_executable(playwright_chromium) -> str | None:
    default = Path(playwright_chromium.executable_path)
    if default.is_file():
        return str(default)

    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        root = Path(local_app_data) / "ms-playwright"
        candidates.extend(sorted(root.glob("chromium-*/chrome-win64/chrome.exe"), reverse=True))

    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if not base:
            continue
        candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
        candidates.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        executable_path = _existing_chromium_executable(p.chromium)
        if not executable_path:
            pytest.skip("Chromium unavailable for recorder bridge tests")
        try:
            browser = p.chromium.launch(headless=True, executable_path=executable_path)
        except Exception as exc:  # pragma: no cover - depends on local browser install
            pytest.skip(f"Chromium unavailable for recorder bridge tests: {exc}")
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture()
def page(browser: Browser) -> Iterator[Page]:
    page = browser.new_page()
    try:
        yield page
    finally:
        page.close()


def _install_bridge(page: Page, html: str, profile: dict | None = None) -> None:
    page.set_content(html)
    # Hover capture is opt-in per recording (see bridge.js's hoverCaptureEnabled gate) —
    # on by default here so the existing hover-heuristic tests below keep exercising the
    # underlying capture logic; test_hover_capture_disabled_by_default covers the gate itself.
    capture_profile = {"input_debounce_ms": 20, "hover_dwell_ms": 35, "hover_capture_enabled": True}
    if profile:
        capture_profile.update(profile)
    page.evaluate(
        """args => {
            window.__events = [];
            window.__ready = [];
            window.__SKILL_CAPTURE_PROFILE__ = args.profile;
            // Like the recorder's sink: a "bridge ready" announcement is bookkeeping, not an event.
            window.__raw = [];       // everything, in arrival order, before any folding
            window.__gaps = [];
            window.__secrets = [];   // hashes of password-like values (session.py keeps them in memory only)
            // Mirrors RecordingSession._apply_amend_sync: facts about a step that already left the page are merged
            // into that step by its uid, so tests keep reading one finished row per action.
            window.__skillReport = (payload) => {
                if (!payload) return;
                window.__raw.push(JSON.parse(JSON.stringify(payload)));
                if (payload._bridge_ready) { window.__ready.push(payload); return; }
                if (payload._gap) { window.__gaps.push(payload); return; }
                if (payload._secret) { window.__secrets.push(payload._secret); return; }
                if (payload._amend) {
                    const row = window.__events.find((e) => e.uid === payload._amend);
                    if (!row) return;
                    for (const k of ["state_change", "post_condition", "surface_context", "post_watch"]) {
                        if (k in payload) row[k] = payload[k];
                    }
                    if (payload.date_field_after != null && row.date_context) row.date_context.field_display_value_after = payload.date_field_after;
                    if (payload.target_label_text && row.target && !row.target.label_text) row.target.label_text = payload.target_label_text;
                    return;
                }
                window.__events.push(payload);
            };
        }""",
        {"profile": capture_profile},
    )
    page.evaluate(BRIDGE_JS)


def test_bridge_announces_itself_over_the_binding_and_again_after_a_document_swap(page: Page) -> None:
    """The recorder skips probing a frame whose page said it is installed (session.py)."""
    _install_bridge(page, "<p>hello</p>")
    assert page.evaluate("() => window.__ready.length") >= 1
    assert _events(page) == []                      # an announcement is never a recorded event

    before = page.evaluate("() => window.__ready.length")
    page.evaluate("() => { document.open(); document.write('<input id=\"x\">'); document.close(); }")
    assert page.evaluate("() => window.__ready.length") > before


def test_bridge_without_a_binding_installs_quietly(page: Page) -> None:
    page.set_content("<p>no binding here</p>")
    page.evaluate(BRIDGE_JS)
    assert page.evaluate("() => window.__SKILL_BRIDGE_V1__ === true")


def _events(page: Page) -> list[dict]:
    return page.evaluate("() => window.__events")


def _type_events(page: Page) -> list[dict]:
    return [event for event in _events(page) if event["action"]["action"] == "type"]


def _action_events(page: Page, action: str) -> list[dict]:
    return [event for event in _events(page) if event["action"]["action"] == action]


def test_native_input_records_single_debounced_type(page: Page) -> None:
    _install_bridge(page, '<input id="email" name="email" aria-label="Email" />')

    page.click("#email")
    page.keyboard.type("person@example.com")
    page.wait_for_timeout(200)

    events = _type_events(page)
    assert len(events) == 1
    assert events[0]["action"]["value"] == "person@example.com"
    assert events[0]["target"]["tag"] == "input"
    assert events[0]["semantic"]["intent_hint"] == "provide_input"


def test_role_textbox_records_type(page: Page) -> None:
    _install_bridge(
        page,
        '<div id="company" role="textbox" contenteditable="true" aria-label="Company"></div>',
    )

    page.click("#company")
    page.keyboard.type("Acme")
    page.wait_for_timeout(80)

    events = _type_events(page)
    assert len(events) == 1
    assert events[0]["action"]["value"] == "Acme"
    assert events[0]["target"]["role"] == "textbox"


def test_contenteditable_records_type(page: Page) -> None:
    _install_bridge(page, '<div id="notes" contenteditable="true" aria-label="Notes"></div>')

    page.click("#notes")
    page.keyboard.type("Follow up")
    page.wait_for_timeout(80)

    events = _type_events(page)
    assert len(events) == 1
    assert events[0]["action"]["value"] == "Follow up"


def test_open_shadow_root_input_records_type_through_composed_path(page: Page) -> None:
    _install_bridge(
        page,
        """
        <shadow-field id="field"></shadow-field>
        <script>
          customElements.define('shadow-field', class extends HTMLElement {
            connectedCallback() {
              const root = this.attachShadow({ mode: 'open' });
              root.innerHTML = '<input id="inner" name="shadow_email" aria-label="Shadow email" />';
            }
          });
        </script>
        """,
    )

    page.locator("shadow-field input").fill("shadow@example.com")
    page.wait_for_timeout(80)

    events = _type_events(page)
    assert len(events) == 1
    assert events[0]["action"]["value"] == "shadow@example.com"
    assert events[0]["target"]["tag"] == "input"
    assert events[0]["target"]["name"] == "shadow_email"


def test_shadow_button_with_empty_own_label_falls_back_to_host_text(page: Page) -> None:
    # Mirrors Shoelace-style wrapper components (<sl-button>Primary</sl-button>): the shadow-
    # internal <button> composedPath()[0] hands back has no innerText/aria-label of its own —
    # the visible label only exists on the light-DOM host. Without the host-text fallback, every
    # such click records with an empty name, leaving only a generic shadow-internal CSS class as
    # a selector — see FIX.md's WF-2-S-J investigation.
    _install_bridge(
        page,
        """
        <shadow-btn id="host">Primary</shadow-btn>
        <script>
          customElements.define('shadow-btn', class extends HTMLElement {
            connectedCallback() {
              const root = this.attachShadow({ mode: 'open' });
              root.innerHTML = '<button></button>';
            }
          });
        </script>
        """,
    )

    page.click("shadow-btn button")
    page.wait_for_timeout(30)

    events = _events(page)
    assert len(events) == 1
    assert events[0]["target"]["tag"] == "button"
    assert events[0]["target"]["inner_text"] == "Primary"
    assert events[0]["shadow_path"]
    assert events[0]["shadow_path"][0]["host"].startswith("shadow-btn")


def test_a_click_records_the_heading_of_its_section_through_shadow_hosts(page: Page) -> None:
    # WF-2 Segment J: two identical widgets under different headings differ only in their section.
    _install_bridge(
        page,
        """
        <h2>Details</h2><shadow-btn>Toggle Me</shadow-btn>
        <h3>Customizing the Summary Icon</h3><shadow-btn id="second">Toggle Me</shadow-btn>
        <script>
          customElements.define('shadow-btn', class extends HTMLElement {
            connectedCallback() { this.attachShadow({ mode: 'open' }).innerHTML = '<button></button>'; }
          });
        </script>
        """,
    )

    page.click("#second >> button")
    page.wait_for_timeout(30)

    (event,) = _events(page)
    assert event["section_heading"] == "Customizing the Summary Icon"


def test_a_titled_icon_with_a_pointing_hand_is_recorded_as_a_click(page: Page) -> None:
    # WF-3 A: demoqa's row trash icon (<span title="Delete"><svg>) has its click wired in script, no attribute.
    _install_bridge(
        page,
        """
        <table><tr><td>Conxa E2</td><td><span id="delete-record-4" title="Delete" style="cursor:pointer">
          <svg width="16" height="16"><path d="M0 0h16v16H0z"></path></svg></span></td></tr></table>
        """,
    )

    page.click("#delete-record-4 path")
    page.wait_for_timeout(30)

    clicks = _action_events(page, "click")
    assert len(clicks) == 1
    assert clicks[0]["target"]["tag"] == "span"


def test_a_titled_icon_inside_a_real_button_or_link_still_records_the_button_or_link(page: Page) -> None:
    _install_bridge(
        page,
        """
        <button id="save" style="cursor:pointer"><span id="save-icon" title="Save">S</span> Save</button>
        <a id="home" href="#home" style="cursor:pointer"><svg aria-label="Home" width="16" height="16"><path id="home-path" d="M0 0h16v16H0z"></path></svg></a>
        """,
    )

    page.click("#save-icon")
    page.click("#home-path")
    page.wait_for_timeout(30)

    assert [c["target"]["tag"] for c in _action_events(page, "click")] == ["button", "a"]


def test_custom_drawer_role_button_records_click(page: Page) -> None:
    _install_bridge(
        page,
        '<aside role="dialog"><div id="create" role="button" aria-label="Create">Create</div></aside>',
    )

    page.click("#create")
    page.wait_for_timeout(30)

    events = _events(page)
    assert len(events) == 1
    assert events[0]["action"]["action"] == "click"
    assert events[0]["target"]["role"] == "button"
    assert events[0]["target"]["aria_label"] == "Create"


def test_menuitem_records_role_and_data_key_in_attributes(page: Page) -> None:
    _install_bridge(
        page,
        '<ul role="menu"><li id="up" role="menuitem" data-key="19" data-state="open">File upload</li></ul>',
    )
    page.click("#up")
    page.wait_for_timeout(30)
    events = _action_events(page, "click")
    assert events, "click was not recorded"
    attrs = events[0]["target"].get("attributes") or {}
    assert attrs.get("role") == "menuitem"
    assert attrs.get("data-key") == "19"
    assert "data-state" not in attrs


def test_click_that_synchronously_reveals_element_is_captured_in_dom_diff(page: Page) -> None:
    # The click listener runs in the capture phase — before the page's own bubble-phase handler
    # fires. If finalizeState() ran synchronously right there (the historical bug), the "after"
    # snapshot would be taken before this handler's appendChild ever executes, so dom_diff would
    # always come back empty even though the click plainly revealed something.
    _install_bridge(
        page,
        """
        <button id="opener">Open</button>
        <script>
          document.getElementById('opener').addEventListener('click', () => {
            const panel = document.createElement('div');
            panel.setAttribute('data-testid', 'reveal-panel');
            panel.textContent = 'Revealed content';
            document.body.appendChild(panel);
          });
        </script>
        """,
    )

    page.click("#opener")
    page.wait_for_timeout(120)

    click_events = _action_events(page, "click")
    assert len(click_events) == 1
    dom_diff = click_events[0]["state_change"]["dom_diff"]
    assert dom_diff is not None
    added = "\n".join(dom_diff["added"])
    assert "reveal-panel" in added


def test_sidebar_form_fields_and_focusable_button_are_recorded(page: Page) -> None:
    _install_bridge(
        page,
        """
        <button id="createContacts">Create contacts</button>
        <div id="menu" hidden>
          <button id="newContact">New</button>
        </div>
        <aside id="drawer" class="right-sidebar" hidden>
          <label>Name <input id="name" name="contact_name" /></label>
          <label>Email <input id="email" name="email" /></label>
          <div id="submitContact" class="primary-action" tabindex="0">Create</div>
        </aside>
        <div id="toast" hidden>Created contact</div>
        <script>
          document.getElementById('createContacts').addEventListener('click', () => {
            document.getElementById('menu').hidden = false;
          });
          document.getElementById('newContact').addEventListener('click', () => {
            document.getElementById('drawer').hidden = false;
          });
          document.getElementById('submitContact').addEventListener('click', () => {
            document.getElementById('toast').hidden = false;
          });
        </script>
        """,
    )

    page.click("#createContacts")
    page.wait_for_timeout(180)
    page.click("#newContact")
    page.wait_for_timeout(180)
    page.fill("#name", "Ada Lovelace")
    page.fill("#email", "ada@example.com")
    page.wait_for_timeout(80)
    page.click("#submitContact")
    page.wait_for_timeout(180)

    events = _events(page)
    actions = [
        (event["action"]["action"], event["target"]["id"], event["action"]["value"])
        for event in events
        if event["action"]["action"] != "hover"
    ]
    assert actions == [
        ("click", "createContacts", None),
        ("click", "newContact", None),
        ("type", "name", "Ada Lovelace"),
        ("type", "email", "ada@example.com"),
        ("click", "submitContact", None),
    ]


def test_generic_text_click_without_ui_change_is_not_recorded(page: Page) -> None:
    _install_bridge(page, '<div id="plain">Plain text</div>')

    page.click("#plain")
    page.wait_for_timeout(180)

    assert _events(page) == []


def test_iframe_sidebar_form_records_when_bridge_runs_as_init_script(browser: Browser) -> None:
    context = browser.new_context()
    init_script = (
        "window.__SKILL_CAPTURE_PROFILE__ = { input_debounce_ms: 20, hover_dwell_ms: 35 };"
        "window.__skillReport = payload => {"
        "  if (payload && (payload._bridge_ready || payload._amend || payload._gap)) return;"
        "  window.top.__events = window.top.__events || [];"
        "  window.top.__events.push(payload);"
        "};\n"
        + BRIDGE_JS
    )
    context.add_init_script(init_script)
    page = context.new_page()
    try:
        page.set_content(
            """
            <script>window.__events = [];</script>
            <iframe
              id="object-builder-ui"
              data-test-id="object-builder-ui-iframe"
              data-selenium-test="associate-panel-iframe"
              data-iframe-ready="true"
              width="600"
              srcdoc='
              <label>First name <input id="firstName" name="firstname" /></label>
              <button id="create">Create</button>
            '></iframe>
            """
        )

        sidebar = page.frame_locator("#object-builder-ui")
        sidebar.locator("#firstName").fill("Ada")
        page.wait_for_timeout(80)
        sidebar.locator("#create").click()
        page.wait_for_timeout(80)

        actions = page.evaluate(
            "() => window.__events.map(event => [event.action.action, event.target.id, event.action.value])"
        )
        # click() moves the real pointer to #create first, which now also records a
        # legitimate "hover" — filter to the type/click pair this test actually covers.
        type_and_click = [a for a in actions if a[0] in ("type", "click")]
        assert type_and_click == [
            ["type", "firstName", "Ada"],
            ["click", "create", None],
        ]
    finally:
        context.close()


def test_focusable_button_click_records_without_immediate_ui_change(page: Page) -> None:
    _install_bridge(page, '<div id="submitContact" class="primary-action" tabindex="0">Create</div>')

    page.click("#submitContact")
    page.wait_for_timeout(40)

    events = _events(page)
    assert len(events) == 1
    assert events[0]["action"]["action"] == "click"
    assert events[0]["target"]["id"] == "submitContact"


def test_a_click_describes_the_element_as_it_was_when_the_press_began(page: Page) -> None:
    """Wikipedia's search box is rebuilt on focus (searchbox -> combobox). A replay meets the box before it is pressed,
    so the step must describe that state, not the rebuilt one the click event lands on."""
    _install_bridge(page, """
      <form id="f"><input id="q" type="search" aria-label="Search Wikipedia"></form>
      <script>
        const q = document.getElementById("q");
        q.addEventListener("focus", () => {
          q.setAttribute("role", "combobox"); q.id = "q2";    // changed in place on first focus
          const hint = document.createElement("div"); hint.textContent = "Search suggestions";
          document.getElementById("f").appendChild(hint);
        }, { once: true });
      </script>""")
    box = page.locator("#q").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(60)

    clicks = [e for e in _events(page) if e["action"]["action"] == "click"]
    assert len(clicks) == 1, page.evaluate("window.__raw")
    assert clicks[0]["target"]["id"] == "q" and clicks[0]["semantic"]["role"] == "searchbox", clicks[0]


def test_typing_into_a_box_the_page_swapped_in_for_the_clicked_one_names_the_clicked_box(page: Page) -> None:
    """Live Wikipedia (2026-11-16): the click lands on #searchInput, then the page replaces it with a new combobox
    input and focuses that; the typing goes into the new one. The typing says which box it stands in for."""
    _install_bridge(page, """
      <form id="f"><input id="searchInput" type="search" aria-label="Search Wikipedia"></form>
      <script>
        document.getElementById("searchInput").addEventListener("click", () => setTimeout(() => {
          const n = document.createElement("input");
          n.setAttribute("role", "combobox"); n.setAttribute("aria-label", "Search Wikipedia");
          document.getElementById("f").replaceChildren(n); n.focus();
        }, 0));
      </script>""")
    box = page.locator("#searchInput").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(60)
    page.keyboard.type("Ada")
    page.wait_for_timeout(120)

    events = _events(page)
    click = next(e for e in events if e["action"]["action"] == "click")
    typed = next(e for e in events if e["action"]["action"] == "type")
    assert click["target"]["id"] == "searchInput"
    assert typed["element_ref"] != click["element_ref"] and typed["stands_in_for"] == click["element_ref"]


def test_enter_in_a_form_field_is_one_step_not_also_a_click_on_the_submit_button(page: Page) -> None:
    """Enter in a field makes the browser click the form's submit button itself. Recording that click as well made a
    replay press Enter and then click Search again on the next page."""
    _install_bridge(page, """
      <form id="f" onsubmit="event.preventDefault(); document.getElementById('out').textContent = 'sent'">
        <input id="q" aria-label="Search"><button id="go" type="submit">Search</button></form><p id="out"></p>""")
    page.click("#q")
    page.keyboard.type("Ada")
    page.keyboard.press("Enter")
    page.wait_for_timeout(120)
    assert page.text_content("#out") == "sent"  # the browser really did submit
    kinds = [(e["action"]["action"], e["target"].get("id")) for e in _events(page)]
    assert ("keyboard_shortcut", "q") in kinds and ("click", "go") not in kinds, kinds
    page.click("#go")  # a real click on the button is still a step
    page.wait_for_timeout(60)
    assert ("click", "go") in [(e["action"]["action"], e["target"].get("id")) for e in _events(page)]


def test_typing_still_on_its_debounce_is_recorded_before_the_enter_that_submits_it(page: Page) -> None:
    """WF-4 Leg D: typed "Automation" and pressed Enter inside the debounce; the typing arrived after the Enter, so the
    replay pressed Enter on the old text. A long debounce keeps the typing pending when Enter is pressed."""
    _install_bridge(page, '<form onsubmit="event.preventDefault()"><input id="q" aria-label="Search"></form>',
                    {"input_debounce_ms": 2000})
    page.click("#q")
    page.keyboard.type("Ada")
    page.keyboard.press("Enter")
    page.wait_for_timeout(120)
    rows = [e for e in _events(page) if e["action"]["action"] != "click"]
    assert [e["action"]["action"] for e in rows] == ["type", "keyboard_shortcut"], rows
    assert rows[0]["action"]["value"] == "Ada"


def test_leaving_the_page_does_not_record_already_sent_typing_again(page: Page) -> None:
    """WF-4 Leg C: type, click Search; the box's change event re-armed the debounce, and the unload flush sent the same
    text a second time after the click, which hid the click's page change from the recorder."""
    _install_bridge(page, '<input id="q" aria-label="Search"><button id="go">Search</button>')
    page.click("#q")
    page.keyboard.type("tshirt")
    page.wait_for_timeout(80)            # the debounce sends it
    page.click("#go")                     # change event re-arms the debounce
    page.evaluate("() => window.dispatchEvent(new Event('beforeunload'))")
    page.evaluate("() => window.__skillFlush()")
    page.wait_for_timeout(60)
    assert [e["action"]["value"] for e in _type_events(page)] == ["tshirt"]


def test_a_click_on_another_element_than_the_press_is_described_at_click_time(page: Page) -> None:
    _install_bridge(page, '<button id="a">Alpha</button><button id="b">Beta</button>')
    a, b = page.locator("#a").bounding_box(), page.locator("#b").bounding_box()
    page.mouse.move(a["x"] + 5, a["y"] + 5)
    page.mouse.down()
    page.mouse.move(b["x"] + 5, b["y"] + 5)
    page.mouse.up()
    page.evaluate("document.getElementById('b').click()")  # the click event a moved press can end with
    page.wait_for_timeout(60)
    assert all(e["target"]["id"] != "a" for e in _events(page) if e["action"]["action"] == "click")


def test_password_input_redacts_value(page: Page) -> None:
    _install_bridge(page, '<input id="password" type="password" name="password" />')

    page.fill("#password", "super-secret")
    page.wait_for_timeout(80)

    events = _type_events(page)
    assert len(events) == 1
    assert events[0]["action"]["value"] == "{{REDACTED}}"
    assert "super-secret" not in str(events[0])
    # Only its hash reaches the recorder, so an address carrying it can be redacted (WF-8 secrets check).
    import hashlib

    assert page.evaluate("window.__secrets") == [hashlib.sha256(b"super-secret").hexdigest()]
    assert "super-secret" not in str(page.evaluate("window.__raw"))


def test_focusout_fallback_records_changed_value_without_input_event(page: Page) -> None:
    _install_bridge(page, '<input id="name" name="name" /><button id="next">Next</button>')

    page.click("#name")
    page.eval_on_selector("#name", "el => { el.value = 'Programmatic change'; }")
    page.click("#next")
    page.wait_for_timeout(80)

    events = _type_events(page)
    assert len(events) == 1
    assert events[0]["action"]["value"] == "Programmatic change"


def test_hover_reveals_menu_records_hover(page: Page) -> None:
    _install_bridge(
        page,
        """
        <nav>
          <div id="crm" role="menuitem" tabindex="0">CRM</div>
        </nav>
        <aside id="drawer" hidden>
          <a href="/contacts" id="contacts">Contacts</a>
        </aside>
        <script>
          document.getElementById('crm').addEventListener('mouseover', () => {
            document.getElementById('drawer').hidden = false;
          });
        </script>
        """,
    )

    page.hover("#crm")
    page.wait_for_timeout(120)

    events = _action_events(page, "hover")
    assert len(events) == 1
    assert events[0]["target"]["id"] == "crm"


def test_hover_capture_disabled_by_default(page: Page) -> None:
    # Hover capture is opt-in per recording (RecordWorkflowDialog's checkbox) — a
    # profile with no hover_capture_enabled flag must record zero hover steps, even
    # on a page that genuinely reveals a menu on hover.
    _install_bridge(
        page,
        """
        <nav>
          <div id="crm" role="menuitem" tabindex="0">CRM</div>
        </nav>
        <aside id="drawer" hidden>
          <a href="/contacts" id="contacts">Contacts</a>
        </aside>
        <script>
          document.getElementById('crm').addEventListener('mouseover', () => {
            document.getElementById('drawer').hidden = false;
          });
        </script>
        """,
        profile={"hover_capture_enabled": False},
    )

    page.hover("#crm")
    page.wait_for_timeout(120)

    assert _action_events(page, "hover") == []


def test_hover_reveals_unmarked_css_only_sibling_records_hover(page: Page) -> None:
    # Mirrors the-internet.herokuapp.com/hovers: a bare <img> with no tag/role/ARIA/class
    # hint reveals a hidden sibling purely via CSS :hover — no JS mouseover listener at all.
    _install_bridge(
        page,
        """
        <style>
          .figcaption { display: none; }
          .figure:hover .figcaption { display: block; }
        </style>
        <div class="figure">
          <img id="avatar" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width="80" height="80" />
          <div class="figcaption">
            <a href="/users/1" id="view-profile">View profile</a>
          </div>
        </div>
        """,
    )

    page.hover("#avatar")
    page.wait_for_timeout(120)

    events = _action_events(page, "hover")
    assert len(events) == 1
    assert events[0]["target"]["id"] == "avatar"


def test_css_only_hover_style_change_records_nothing(page: Page) -> None:
    _install_bridge(
        page,
        """
        <style>
          #plain:hover { color: rgb(255, 0, 0); background: rgb(240, 240, 240); }
        </style>
        <button id="plain">Plain button</button>
        """,
    )

    page.hover("#plain")
    page.wait_for_timeout(120)

    assert _events(page) == []


def test_hover_target_that_collapses_before_emit_records_nothing(page: Page) -> None:
    _install_bridge(
        page,
        """
        <div id="loading" role="status" style="width: 80px; height: 24px;">Loading</div>
        <aside id="drawer" hidden>
          <button>Ready</button>
        </aside>
        <script>
          document.getElementById('loading').addEventListener('mouseover', () => {
            document.getElementById('loading').style.width = '0px';
            document.getElementById('loading').style.height = '0px';
            document.getElementById('drawer').hidden = false;
          });
        </script>
        """,
    )

    page.dispatch_event("#loading", "mouseover", {"bubbles": True})
    page.wait_for_timeout(120)

    assert _action_events(page, "hover") == []


def test_hover_then_click_revealed_child_records_hover_before_click(page: Page) -> None:
    _install_bridge(
        page,
        """
        <nav>
          <div id="crm" role="menuitem" tabindex="0">CRM</div>
        </nav>
        <aside id="drawer" hidden>
          <button id="contacts">Contacts</button>
        </aside>
        <script>
          document.getElementById('crm').addEventListener('mouseover', () => {
            document.getElementById('drawer').hidden = false;
          });
        </script>
        """,
        profile={"hover_dwell_ms": 1000},
    )

    page.hover("#crm")
    page.wait_for_timeout(80)
    page.click("#contacts")
    page.wait_for_timeout(80)

    actions = [event["action"]["action"] for event in _events(page)]
    assert actions[:2] == ["hover", "click"]
    assert _events(page)[0]["target"]["id"] == "crm"
    assert _events(page)[1]["target"]["id"] == "contacts"


def test_repeated_hover_over_unchanged_target_does_not_duplicate(page: Page) -> None:
    _install_bridge(
        page,
        """
        <nav>
          <div id="crm" role="menuitem" tabindex="0">CRM</div>
        </nav>
        <aside id="drawer" hidden>
          <button id="contacts">Contacts</button>
        </aside>
        <script>
          document.getElementById('crm').addEventListener('mouseover', () => {
            document.getElementById('drawer').hidden = false;
          });
        </script>
        """,
    )

    page.hover("#crm")
    page.wait_for_timeout(120)
    page.mouse.move(5, 5)
    page.wait_for_timeout(100)
    page.hover("#crm")
    page.wait_for_timeout(120)

    events = _action_events(page, "hover")
    assert len(events) == 1


def test_hover_over_static_heading_on_a_link_rich_page_records_nothing(page: Page) -> None:
    # The mouse rests on a static heading for longer than the dwell timer on its way to a real
    # target. Nothing is revealed, so nothing should be recorded — the page's OWN links must
    # never be mistaken for something the hover surfaced.
    #
    # The bridge is installed against an EMPTY document and the content added afterwards, which
    # is what document_start injection looks like in production: the script loads while the page
    # is still parsing. The reveal baseline must be taken once the document is ready, not while
    # it is blank — against a blank baseline every link the page renders counts as "revealed",
    # and the first hover after any page load was recorded as a step.
    _install_bridge(page, "<div id='root'></div>")
    page.evaluate(
        """() => {
            const links = Array.from({length: 40}, (_, i) =>
                `<li><a href="/x${i}">Example ${i}</a></li>`).join("");
            document.getElementById("root").innerHTML =
                `<h2 id="title">Available Examples</h2><ul>${links}</ul>`;
        }"""
    )
    page.wait_for_timeout(150)

    page.hover("#title")
    page.wait_for_timeout(200)

    assert _action_events(page, "hover") == []


def test_hover_after_scrolling_records_nothing(page: Page) -> None:
    # Scrolling brings dozens of off-screen links on screen. A viewport-clipped visibility test
    # reads that as "newly revealed", so the next element the mouse rested on was recorded as a
    # hover step that revealed nothing. Reveal detection must be scroll-independent.
    links = "".join(f'<li><a href="/x{i}">Example {i}</a></li>' for i in range(120))
    _install_bridge(
        page,
        f"<div style='height:1200px'>spacer</div><ul>{links}</ul><h2 id='footer-title'>The End</h2>",
    )

    page.mouse.wheel(0, 3000)
    page.wait_for_timeout(120)
    page.hover("#footer-title")
    page.wait_for_timeout(200)

    assert _action_events(page, "hover") == []


def test_radio_group_records_choice_context_with_every_option(page: Page) -> None:
    """Regression: recording a gender radio group used to produce a step named after the
    group's shared `name` attribute (role=radio[name="gender"]) with no record of the other
    options -- see CLAUDE.md's multiple-choice plan. buildChoiceContext must capture every
    sibling in the group, not just the one clicked."""
    _install_bridge(
        page,
        """
        <fieldset>
          <legend>Gender</legend>
          <label><input type="radio" name="gender" value="male" id="g-male"> Male</label>
          <label><input type="radio" name="gender" value="female" id="g-female"> Female</label>
          <label><input type="radio" name="gender" value="other" id="g-other"> Other</label>
        </fieldset>
        """,
    )

    page.click("#g-female")
    page.wait_for_timeout(80)

    events = _action_events(page, "set_radio")
    assert len(events) == 1
    choice = events[0]["choice_context"]
    assert choice["kind"] == "radio"
    assert choice["group_key"] == "gender"
    assert choice["group_label"] == "Gender"
    assert [o["value"] for o in choice["options"]] == ["male", "female", "other"]
    picked = next(o for o in choice["options"] if o["value"] == "female")
    assert picked["checked"] is True


def test_radio_click_does_not_also_record_a_separate_click_or_focus_event(page: Page) -> None:
    """The other half of the screenshot bug: a click on a radio option must not ALSO survive as
    its own click/focus event once the committing set_radio event exists for the same element --
    that pairing is what step_anchors.clean_steps collapses into one compiled step."""
    _install_bridge(
        page,
        """
        <input type="radio" name="plan" value="pro" id="plan-pro">
        <input type="radio" name="plan" value="free" id="plan-free">
        """,
    )

    page.click("#plan-pro")
    page.wait_for_timeout(80)

    assert len(_action_events(page, "set_radio")) == 1
    # The click handler still fires (bridge.js records it before the change-driven set_radio) --
    # that's expected and is exactly what clean_steps's prep-click merge exists to absorb at
    # compile time; this test only guards that set_radio itself is never duplicated.


def test_checkbox_group_records_choice_context(page: Page) -> None:
    _install_bridge(
        page,
        """
        <fieldset>
          <legend>Sports</legend>
          <input type="checkbox" name="sports" value="soccer" id="s-soccer">
          <input type="checkbox" name="sports" value="tennis" id="s-tennis">
          <input type="checkbox" name="sports" value="chess" id="s-chess">
        </fieldset>
        """,
    )

    page.click("#s-soccer")
    page.wait_for_timeout(80)

    events = _action_events(page, "set_checkbox")
    assert len(events) == 1
    choice = events[0]["choice_context"]
    assert choice["kind"] == "checkbox"
    assert choice["multi"] is True
    assert [o["value"] for o in choice["options"]] == ["soccer", "tennis", "chess"]


def test_lone_checkbox_with_no_group_gets_no_choice_context(page: Page) -> None:
    """A standalone checkbox ("I agree") isn't a multiple-choice control -- bridge.js requires
    >= 2 group members before treating a checkbox as part of a choice group."""
    _install_bridge(page, '<input type="checkbox" id="agree" name="agree">')

    page.click("#agree")
    page.wait_for_timeout(80)

    events = _action_events(page, "set_checkbox")
    assert len(events) == 1
    assert events[0]["choice_context"] is None


def test_select_records_choice_context(page: Page) -> None:
    _install_bridge(
        page,
        """
        <select id="country" aria-label="Country">
          <option value="us">United States</option>
          <option value="ca">Canada</option>
          <option value="mx">Mexico</option>
        </select>
        """,
    )

    page.select_option("#country", "ca")
    page.wait_for_timeout(80)

    events = _action_events(page, "select")
    assert len(events) == 1
    choice = events[0]["choice_context"]
    assert choice["kind"] == "select"
    assert [o["value"] for o in choice["options"]] == ["us", "ca", "mx"]


# ── Unmatchable-selector regressions (demoqa practice form) ──────────────────
#
# buildTextSelector and buildXPath each emitted a selector that could never match the
# element it was derived from, and both occupied slots in the compiled bundle ahead of
# signals that could. See tests/test_popup_choice_identity.py for the compile-side half.


def test_select_gets_no_text_selector_from_its_option_list(page: Page) -> None:
    # A <select>'s innerText is its concatenated <option> text. Playwright's text engine
    # never matches a <select> by that, so recording one guarantees a replay miss.
    _install_bridge(
        page,
        '<select id="year"><option>1900</option><option>1901</option><option>1902</option></select>',
    )
    page.select_option("#year", "1901")
    page.wait_for_timeout(120)

    events = _action_events(page, "select")
    assert events, "no select event recorded"
    assert events[0]["selectors"]["text_based"] == ""


def test_text_selector_is_dropped_rather_than_truncated(page: Page) -> None:
    # `text="…"` is an EXACT match, so a truncated string can never match its own element.
    long_text = "word " * 40  # comfortably over the 80-char cap
    _install_bridge(page, f'<div role="button" id="b" tabindex="0">{long_text}</div>')
    page.click("#b")
    page.wait_for_timeout(120)

    events = _action_events(page, "click")
    assert events, "no click event recorded"
    assert events[0]["selectors"]["text_based"] == ""


def test_short_text_still_produces_a_text_selector(page: Page) -> None:
    _install_bridge(page, '<div role="button" id="b" tabindex="0">Submit</div>')
    page.click("#b")
    page.wait_for_timeout(120)

    events = _action_events(page, "click")
    assert events, "no click event recorded"
    assert events[0]["selectors"]["text_based"] == 'text="Submit"'


def test_depth_capped_xpath_is_relative_not_absolute(page: Page) -> None:
    # buildXPath stops at xpath_max_depth. Emitting `/a/b/c` for an element deeper than the
    # cap claims an absolute path from the document root, which matches nothing.
    depth = 16
    html = "".join(f"<div id='d{i}'>" for i in range(depth))
    html += '<button id="deep">Go</button>' + "</div>" * depth
    _install_bridge(page, html)
    page.click("#deep")
    page.wait_for_timeout(120)

    events = _action_events(page, "click")
    assert events, "no click event recorded"
    xpath = events[0]["selectors"]["xpath"]
    assert xpath.startswith("//"), f"depth-capped xpath must be relative, got {xpath!r}"
    assert page.locator(f"xpath={xpath}").count() >= 1, f"{xpath!r} matched nothing"


def test_shallow_xpath_stays_absolute_and_matches(page: Page) -> None:
    _install_bridge(page, '<div><button id="shallow">Go</button></div>')
    page.click("#shallow")
    page.wait_for_timeout(120)

    events = _action_events(page, "click")
    assert events, "no click event recorded"
    xpath = events[0]["selectors"]["xpath"]
    assert not xpath.startswith("//")
    assert page.locator(f"xpath={xpath}").count() == 1


def test_popup_listbox_records_the_control_that_opens_it(page: Page) -> None:
    # The options only exist while the menu is open, and the opening click lands on a
    # role-less container the recorder discards — so the opener has to be captured here or
    # the selection can never be replayed.
    _install_bridge(
        page,
        """
        <div id="wrap">
          <input id="combo" role="combobox" aria-controls="lb" aria-expanded="true" />
          <div id="lb" role="listbox">
            <div role="option" id="o0">NCR</div>
            <div role="option" id="o1">Haryana</div>
          </div>
        </div>
        """,
    )
    page.click("#o0")
    page.wait_for_timeout(150)

    events = _action_events(page, "select_option")
    assert events, "aria listbox click was not recorded as select_option"
    cc = events[0]["choice_context"]
    assert cc is not None and cc["kind"] == "aria_listbox"
    assert cc["opener_selector"] == "#combo"


def test_always_visible_group_records_no_opener(page: Page) -> None:
    # A radiogroup is never hidden behind a popup — there is nothing to open.
    _install_bridge(
        page,
        """
        <div role="radiogroup" id="rg">
          <div role="radio" id="r0" data-value="a">A</div>
          <div role="radio" id="r1" data-value="b">B</div>
        </div>
        """,
    )
    page.click("#r0")
    page.wait_for_timeout(150)

    events = _action_events(page, "set_radio")
    assert events, "aria radio click was not recorded as set_radio"
    assert events[0]["choice_context"]["opener_selector"] == ""


# ── Calendar day cells (demoqa date-of-birth regression) ─────────────────────
#
# A react-datepicker day is `<div role="gridcell" tabindex="-1">`. isInteractiveNode's role
# allow-list omitted "gridcell" and the tabindex check deliberately rejects -1 (roving-tabindex
# widgets give every unfocused item -1), so resolveMeaningfulTarget found no interactive
# ancestor and returned null: the click that actually COMMITS the date was never recorded. The
# replayed run changed year and month, the calendar visibly moved, and the field kept its
# original date.


def _calendar_html(aria_label: str = "Choose Thursday, March 15th, 2007") -> str:
    cells = "".join(
        f'<div role="gridcell" tabindex="-1" class="day" aria-label="d{i}">{i}</div>'
        for i in range(1, 29)
    )
    return f"""
    <input id="dob" class="form-control" />
    <div role="grid" class="datepicker">
      <div class="header">March 2007</div>
      <button aria-label="Previous Month">&lt;</button>
      <button aria-label="Next Month">&gt;</button>
      {cells}
      <div role="gridcell" tabindex="-1" class="day" id="target" aria-label="{aria_label}">15</div>
    </div>
    """


def test_calendar_day_cell_click_is_recorded(page: Page) -> None:
    _install_bridge(page, _calendar_html())
    page.click("#target")
    page.wait_for_timeout(150)

    events = _action_events(page, "click")
    assert events, "a role=gridcell day cell click was not recorded at all"
    assert events[0]["target"]["role"] == "gridcell"


def test_calendar_day_click_carries_the_parsed_date(page: Page) -> None:
    # aria-label prose + an ordinal suffix ("15th") both defeat a bare Date.parse.
    _install_bridge(page, _calendar_html())
    page.click("#target")
    page.wait_for_timeout(150)

    events = _action_events(page, "click")
    dc = events[0]["date_context"]
    assert dc is not None, "no date_context on a calendar day click"
    assert dc["role"] == "day"
    assert dc["iso_date"] == "2007-03-15"


def test_recorded_date_is_not_shifted_by_the_local_timezone(page: Page) -> None:
    # The parsed date is LOCAL midnight; formatting it through toISOString() moves it back a day
    # on every positive-offset zone (IST, CET, …). This asserts the calendar day, not UTC's.
    _install_bridge(page, _calendar_html("January 1st, 2020"))
    page.click("#target")
    page.wait_for_timeout(150)

    dc = _action_events(page, "click")[0]["date_context"]
    assert dc["iso_date"] == "2020-01-01", "date shifted a day — toISOString() is back"


@pytest.mark.parametrize(
    "label,expected",
    [("2007-03-15", "2007-03-15"), ("March 15, 2007", "2007-03-15"), ("15 March 2007", "2007-03-15")],
)
def test_iso_and_plain_aria_date_formats_still_parse(page: Page, label: str, expected: str) -> None:
    # A fresh page per case: bridge.js installs once per JS context (__SKILL_BRIDGE_V1__).
    _install_bridge(page, _calendar_html(label))
    page.click("#target")
    page.wait_for_timeout(150)
    dc = _action_events(page, "click")[0]["date_context"]
    assert dc["iso_date"] == expected, f"{label!r} parsed as {dc['iso_date']!r}"


@pytest.mark.parametrize(
    "role", ["treeitem", "menuitemcheckbox", "menuitemradio", "searchbox", "spinbutton"]
)
def test_other_composite_widget_item_roles_are_recorded(page: Page, role: str) -> None:
    # gridcell was one omission in a list that means "an individual activatable item".
    _install_bridge(page, f'<div role="{role}" id="it" tabindex="-1">X</div>')
    page.click("#it")
    page.wait_for_timeout(120)
    assert _action_events(page, "click"), f"role={role} click was not recorded"


def test_container_roles_are_still_not_treated_as_the_target(page: Page) -> None:
    # A click must resolve to the ITEM, never the surrounding widget — recording the container
    # would replay against the wrong element.
    _install_bridge(page, '<div role="listbox" id="box" style="padding:40px">plain area</div>')
    page.click("#box", position={"x": 5, "y": 5})
    page.wait_for_timeout(150)
    assert not _action_events(page, "click"), "a bare listbox container was recorded as a target"


def test_menu_container_with_tabindex_resolves_click_to_the_item(page: Page) -> None:
    # jQuery UI's autocomplete/menu widget puts tabindex="0" on the <ul> itself while each
    # item's clickable node is tabindex="-1" (isInteractiveNode rejects it, and a bare <li>
    # matches nothing). Without a container guard, resolveMeaningfulTarget's tabindex fallback
    # walks past the item and accepts the <ul> — recording "Java JavaScript" (both options
    # concatenated) as the target instead of the one option actually clicked.
    _install_bridge(
        page,
        """
        <ul id="menu" tabindex="0" class="ui-menu ui-autocomplete">
          <li class="ui-menu-item"><div tabindex="-1">Java</div></li>
          <li class="ui-menu-item"><div tabindex="-1">JavaScript</div></li>
        </ul>
        """,
    )
    page.click("#menu >> text=JavaScript")
    page.wait_for_timeout(120)

    events = _action_events(page, "click")
    assert events, "click on the menu item was not recorded"
    target = events[0]["target"]
    assert target["tag"] == "li", f"resolved to {target['tag']!r} instead of the clicked <li>"
    assert target["inner_text"] == "JavaScript"


def test_calendar_root_is_the_widget_not_the_day_cell(page: Page) -> None:
    # CALENDAR_ROOT_SELECTORS matches on class SUBSTRINGS ("[class*='datepicker']"), and every
    # descendant of a widget repeats the library prefix — react-datepicker's day cell is
    # `react-datepicker__day`, which matches that selector itself. findCalendarRoot stopped at
    # the first match walking up, so the CELL became the "grid": date_context.grid and .cell came
    # back as the same date-specific selector, the runtime scoped its day search inside the
    # recorded cell (which has no day-number descendant, only its own text), and the header /
    # prev / next / year+month selects that all live ABOVE the cell came back empty.
    _install_bridge(
        page,
        """
        <div class="react-datepicker">
          <div class="react-datepicker__header">March 2007</div>
          <select class="react-datepicker__year-select"><option>2007</option></select>
          <select class="react-datepicker__month-select"><option>March</option></select>
          <div class="react-datepicker__month">
            <div class="react-datepicker__day" id="d15" role="gridcell" tabindex="-1"
                 aria-label="Choose Thursday, March 15th, 2007">15</div>
          </div>
        </div>
        """,
    )
    page.click("#d15")
    page.wait_for_timeout(150)

    dc = _action_events(page, "click")[0]["date_context"]
    assert dc["role"] == "day"
    assert dc["grid"] != dc["cell"], "the day cell was reported as its own calendar grid"
    assert "aria-label" not in dc["grid"], (
        f"grid is a date-specific selector ({dc['grid']!r}) — it can only ever match the "
        "recorded day, so a different target date could never be picked"
    )


def _jqueryui_style_html(month_attr: str = "10", year_attr: str = "2026", header: str = "November 2026") -> str:
    # jQuery UI's actual stock markup: the day <td> carries data-month (0-indexed)/data-year, the
    # clicked <a> itself carries no attribute and no aria-label/title at all — just the bare day
    # number as text. No test fixture before this covered that shape; every other calendar test
    # here gives the day cell a full aria-label sentence, which is exactly why this bug shipped.
    return f"""
    <input id="dob" class="form-control" />
    <div class="ui-datepicker">
      <div class="ui-datepicker-header">
        <a class="ui-datepicker-prev" title="Prev">&lt;</a>
        <a class="ui-datepicker-next" title="Next">&gt;</a>
        <div class="ui-datepicker-title">{header}</div>
      </div>
      <table class="ui-datepicker-calendar">
        <tbody>
          <tr>
            <td class="ui-datepicker-other-month" data-month="{month_attr}" data-year="{year_attr}" id="day30overflow">
              <a class="ui-state-default" href="#">30</a>
            </td>
            <td data-handler="selectDay" data-event="click" data-month="{month_attr}" data-year="{year_attr}" id="day15">
              <a class="ui-state-default" href="#">15</a>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    """


def test_jqueryui_style_day_cell_with_no_attributes_is_still_tagged(page: Page) -> None:
    # The recorder-side fix: _cellIsoDate alone returns null for this markup (no data-date, no
    # aria-label/title) — buildDateContext must fall back to the cell's own day-number text plus
    # the ancestor <td>'s data-month/data-year (jQuery UI's real convention, 0-indexed).
    _install_bridge(page, _jqueryui_style_html())
    page.click("#day15 a")
    page.wait_for_timeout(150)

    dc = _action_events(page, "click")[0]["date_context"]
    assert dc is not None, "a bare-text jQuery UI day cell click was not tagged with date_context at all"
    assert dc["role"] == "day"
    assert dc["iso_date"] == "2026-11-15"
    assert dc["cell_attr"] == "", "no machine attribute exists on this markup — attr must report empty"


def test_jqueryui_style_day_cell_falls_back_to_header_text_without_data_attrs(page: Page) -> None:
    # Same bare-text day cell, but with no data-month/data-year anywhere — only the grid's own
    # header text ("November 2026") to derive month/year from.
    html = """
    <div class="ui-datepicker">
      <div class="ui-datepicker-header">
        <a class="ui-datepicker-prev" title="Prev">&lt;</a>
        <a class="ui-datepicker-next" title="Next">&gt;</a>
        <div class="ui-datepicker-title">November 2026</div>
      </div>
      <table class="ui-datepicker-calendar">
        <tbody><tr><td id="day15"><a class="ui-state-default" href="#">15</a></td></tr></tbody>
      </table>
    </div>
    """
    _install_bridge(page, html)
    page.click("#day15 a")
    page.wait_for_timeout(150)

    dc = _action_events(page, "click")[0]["date_context"]
    assert dc is not None
    assert dc["role"] == "day"
    assert dc["iso_date"] == "2026-11-15"


def test_jqueryui_other_month_overflow_cell_is_not_tagged_as_a_day_pick(page: Page) -> None:
    # The greyed "30" cell from the previous month (jQuery UI's selectOtherMonths mode makes it
    # clickable), carrying the SAME data-month/data-year as the real day-15 cell — jQuery UI
    # stamps overflow cells with the visible month, not their own. Without the overflow/disabled
    # exclusion this would tag as day 30 of the visible month instead of being left alone as an
    # ordinary click — exactly the ambiguity the runtime's own dayNumberSelector() already guards
    # against on the replay side.
    _install_bridge(page, _jqueryui_style_html())
    page.click("#day30overflow a")
    page.wait_for_timeout(150)

    dc = _action_events(page, "click")[0]["date_context"]
    assert dc is None, "an other-month overflow cell was tagged as a real day pick"


def test_sibling_combobox_value_is_not_captured_as_this_fields_label(page: Page) -> None:
    # captureAssociatedLabel's fallback #6 walks preceding siblings looking for a short text
    # caption. A react-select-style "City" field sitting right after a "State" field picked up
    # the STATE field's own rendered selected-value text ("Uttar Pradesh") as its label — that
    # text is the OTHER field's live answer, not a caption for this one, and it changes
    # independently of what this field actually is (mega-workflow investigation: this exact
    # shape produced label_text="Uttar Pradesh" on the City input, which the compiler then used
    # to build a near-zero-confidence `label:has-text("Uttar Pradesh") + input` selector).
    _install_bridge(
        page,
        """
        <div id="stateCity-wrapper">
          <div class="col">
            <div id="state" class="css-container">
              <div class="css-value-container">
                <div class="css-single-value">Uttar Pradesh</div>
                <input id="state-input" />
              </div>
            </div>
          </div>
          <div class="col">
            <div id="city" class="css-container">
              <div class="css-value-container">
                <input id="city-input" />
              </div>
            </div>
          </div>
        </div>
        """,
    )
    page.click("#city-input")
    page.keyboard.type("L")
    page.wait_for_timeout(200)

    events = _type_events(page)
    assert len(events) == 1
    assert events[0]["target"]["label_text"] != "Uttar Pradesh"


def test_a_page_heading_is_not_a_boxs_caption_and_a_link_gets_no_neighbour_caption(page: Page) -> None:
    # W5-b (2026-11-15 run): on a flat form the page title was the only text above the boxes, so every box was named
    # after the page ("Clicked Shapeshifter") and the compiler invented an input from it. A link inside a list was
    # likewise named after the paragraph beside it. Neither is a caption: headings are skipped, and only a form
    # control has a caption at all.
    _install_bridge(
        page,
        """
        <h1>Shapeshifter</h1>
        <div><input id="plain" /></div>
        <div><span>Email address</span><input id="real" /></div>
        <p>Open pull requests for the repository</p>
        <a id="lnk" href="#x">First change</a>
        """,
    )
    page.click("#plain")
    page.keyboard.type("a")
    page.wait_for_timeout(200)
    page.click("#real")
    page.keyboard.type("b")
    page.wait_for_timeout(200)
    page.click("#lnk")
    page.wait_for_timeout(150)

    typed = _type_events(page)
    labels = {e["target"]["id"]: e["target"].get("label_text") for e in typed}
    assert labels["plain"] in (None, ""), f"a heading became the box's caption: {labels['plain']!r}"
    assert labels["real"] == "Email address"
    click = _action_events(page, "click")[-1]
    assert not click["target"].get("label_text"), "a link was captioned by its neighbour"


# ── "capture a value" (gap analysis G4) ─────────────────────────────────────────────────────


def test_capture_mode_reads_the_next_click_instead_of_acting_on_it(page: Page) -> None:
    _install_bridge(
        page,
        '<button id="send" onclick="window.__sent = true">Send</button>'
        '<span id="total" data-testid="order-total">Total: $42.10</span>',
    )

    assert page.evaluate("() => window.__conxaCapture(true)") is True
    page.click("#total")
    page.wait_for_timeout(60)

    events = _action_events(page, "extract")
    assert len(events) == 1
    assert events[0]["extract"] == {"source": "text"}
    assert events[0]["target"]["inner_text"] == "Total: $42.10"
    assert _action_events(page, "click") == []                 # recorded as a read, never as a click
    assert page.evaluate("() => document.documentElement.style.cursor") == ""   # one shot: capture turned itself off

    page.click("#send")                                         # back to normal: now it IS a click, and it acts
    page.wait_for_timeout(60)
    assert page.evaluate("() => window.__sent === true")
    assert len(_action_events(page, "click")) == 1


def test_a_captured_click_never_reaches_the_page(page: Page) -> None:
    _install_bridge(page, '<button id="danger" onclick="window.__fired = true">Delete all</button>')
    page.evaluate("() => window.__conxaCapture(true)")
    page.click("#danger")
    page.wait_for_timeout(60)
    assert page.evaluate("() => window.__fired === true") is False   # reading a Delete button must not press it
    assert len(_action_events(page, "extract")) == 1


def test_capture_mode_can_be_cancelled_and_reads_an_inputs_value(page: Page) -> None:
    _install_bridge(page, '<input id="ref" value="PO-77"><button id="b">B</button>')
    page.evaluate("() => window.__conxaCapture(true)")
    assert page.evaluate("() => window.__conxaCapture(false)") is False
    page.click("#b")
    page.wait_for_timeout(60)
    assert _action_events(page, "extract") == []

    page.evaluate("() => window.__conxaCapture(true)")
    page.click("#ref")
    page.wait_for_timeout(60)
    assert _action_events(page, "extract")[0]["extract"] == {"source": "value"}


def test_calendar_day_click_reports_what_the_field_held_before_and_after(page: Page) -> None:
    # The outcome the compiler judges a pick by: the date field after the click vs before it.
    _install_bridge(page, _calendar_html())
    page.evaluate(
        "document.querySelector('#target').addEventListener('click', () => { document.querySelector('#dob').value = '03/15/2007'; })"
    )
    page.click("#dob")
    page.click("#target")
    page.wait_for_timeout(400)

    dc = _action_events(page, "click")[-1]["date_context"]
    assert dc["field_display_value"] == ""
    assert dc["field_display_value_after"] == "03/15/2007"
    assert "__fieldEl" not in dc


def test_calendar_day_click_that_leaves_the_field_alone_reports_the_same_value(page: Page) -> None:
    _install_bridge(page, _calendar_html())
    page.click("#dob")
    page.click("#target")
    page.wait_for_timeout(400)

    dc = _action_events(page, "click")[-1]["date_context"]
    assert dc["field_display_value_after"] == dc["field_display_value"]


def _anchors_for(page: Page, html: str) -> list[dict]:
    _install_bridge(page, html)
    page.click("#go")
    page.wait_for_timeout(150)
    return _action_events(page, "click")[-1]["anchors"]


def test_anchor_in_an_alert_region_or_a_fixed_overlay_is_flagged_ephemeral(page: Page) -> None:
    anchors = _anchors_for(
        page,
        '<div data-testid="promo" style="position:fixed;top:0">Join our list</div>'
        '<div role="status" data-testid="live">Saved</div>'
        '<main>Orders<button id="go">Go</button></main>',
    )
    flagged = {a["element"] for a in anchors if a.get("ephemeral")}
    assert {"Join our list", "Saved"} <= flagged
    assert all(not a.get("ephemeral") for a in anchors if a["element"].startswith("Orders"))


def test_fixed_landmark_and_dialog_anchors_are_not_flagged(page: Page) -> None:
    anchors = _anchors_for(
        page,
        '<header style="position:fixed;top:0">Acme</header>'
        '<div role="dialog" style="position:fixed" aria-label="Edit order"><button id="go">Go</button></div>',
    )
    assert anchors and not any(a.get("ephemeral") for a in anchors)


# ── Month grids found by structure, not by library class name ────────────────
#
# A date picker nobody wrote a selector for: plain divs, no role, no "calendar"/"datepicker" class.
# What it is - 28+ day numbers under a "March 2007" header - is enough to tag the click.


def _unknown_picker_html(days: int = 31, header: str = "March 2007") -> str:
    cells = "".join(f'<div class="c" tabindex="0">{i}</div>' for i in range(1, days + 1))
    head = f'<div class="t">{header}</div>' if header else ""
    return f'<input id="dob"><div class="pop">{head}<div class="body">{cells}</div></div>'


def test_a_month_grid_with_no_known_class_or_role_is_recognised_by_its_structure(page: Page) -> None:
    _install_bridge(page, _unknown_picker_html())
    page.click("text=15")
    page.wait_for_timeout(150)
    dc = _action_events(page, "click")[0]["date_context"]
    assert dc is not None and dc["role"] == "day"
    assert dc["iso_date"] == "2007-03-15"


def test_a_short_run_of_numbers_is_not_a_month_grid(page: Page) -> None:
    _install_bridge(page, _unknown_picker_html(days=7))        # a week strip
    page.click("text=5")
    page.wait_for_timeout(150)
    assert _action_events(page, "click")[0].get("date_context") is None


def test_numbers_without_a_month_header_are_not_a_month_grid(page: Page) -> None:
    _install_bridge(page, _unknown_picker_html(header=""))     # e.g. a page-number strip
    page.click("text=15")
    page.wait_for_timeout(150)
    assert _action_events(page, "click")[0].get("date_context") is None


def test_an_unknown_picker_reports_its_field_and_what_the_pick_left_in_it(page: Page) -> None:
    """The structural route feeds the same outcome check as a known picker: the field is found (the root
    is the widget, not something bigger) and its value after the click is recorded."""
    _install_bridge(page, _unknown_picker_html())
    page.evaluate(
        "() => document.querySelectorAll('.c').forEach(c => c.addEventListener('click', () =>"
        " { document.getElementById('dob').value = '03/' + c.textContent.padStart(2, '0') + '/2007'; }))"
    )
    page.click("#dob")
    page.click("text=15")
    page.wait_for_timeout(250)
    dc = [e for e in _action_events(page, "click") if e.get("date_context")][0]["date_context"]
    assert dc["field"].endswith("dob") or "dob" in dc["field"]
    assert dc["field_display_value_after"] == "03/15/2007"


# ── what the app said after an action (EXEC-49 G9) ──────────────────────────────────────────


def _click_and_wait(page: Page, selector: str) -> dict:
    page.click(selector)
    page.wait_for_timeout(700)
    events = _action_events(page, "click")
    assert events, "no click was recorded"
    return events[-1]


def test_a_new_alert_after_a_click_is_recorded_as_value_free_words(page: Page) -> None:
    _install_bridge(
        page,
        """<button id="save" onclick="
            const a = document.createElement('div');
            a.setAttribute('role', 'alert');
            a.textContent = 'The name “Q3 report 2026” already exists. Contact jo@acme.com (ticket 4411).';
            document.body.appendChild(a);">Save</button>""",
    )
    event = _click_and_wait(page, "#save")
    ctx = event["surface_context"]

    assert ctx is not None, {k: event[k] for k in event if k in ("state_change", "surface_context")}
    assert [i["kind"] for i in ctx["items"]] == ["alert"]
    words = ctx["items"][0]["words"]
    assert "already" in words and "exists" in words
    assert not {"q3", "report", "jo", "acme", "2026", "4411"} & set(words), f"a value leaked: {words}"


def test_a_message_that_was_already_on_the_page_is_not_attributed_to_the_click(page: Page) -> None:
    _install_bridge(page, '<div role="alert">Heads up: maintenance tonight</div><button id="go">Go</button>')

    assert _click_and_wait(page, "#go")["surface_context"] is None


def test_an_invalid_field_message_is_recorded_too(page: Page) -> None:
    _install_bridge(
        page,
        """<input id="email" aria-describedby="err"><span id="err" hidden>Email is invalid</span>
        <button id="send" onclick="
            document.getElementById('email').setAttribute('aria-invalid', 'true');
            document.getElementById('err').hidden = false;">Send</button>""",
    )
    ctx = _click_and_wait(page, "#send")["surface_context"]

    assert ctx is not None and ctx["items"][0]["kind"] == "invalid" and "invalid" in ctx["items"][0]["words"]


def test_a_click_that_changes_no_message_records_no_surface_context(page: Page) -> None:
    _install_bridge(page, '<button id="plain">Plain</button>')

    assert _click_and_wait(page, "#plain")["surface_context"] is None


# ── what the app displayed while it finished the work (Proof of Done) ───────────────────────────

_UPLOAD_TRAY_HTML = """
<button id="upload" onclick="
  const tray = document.createElement('div');
  tray.setAttribute('role', 'dialog');
  tray.innerHTML = '<p>Uploading 1 item</p><div role=progressbar></div>';
  document.body.appendChild(tray);
  setTimeout(() => { tray.innerHTML = '<p>1 upload complete</p>'; }, 900);">Upload</button>
"""


def _wait_for_event(page: Page, action: str, timeout_ms: int = 6000, *, watch: bool = False) -> dict:
    """The newest ``action`` row. A row leaves the page the moment the person acts and its details follow as
    amendments, so ``watch=True`` also waits for the progress watch's amendment (``post_watch``)."""
    waited = 0
    while waited < timeout_ms:
        events = _action_events(page, action)
        if events and (not watch or events[-1].get("post_watch")):
            return events[-1]
        page.wait_for_timeout(100)
        waited += 100
    if watch and _action_events(page, action):
        return _action_events(page, action)[-1]  # no watch amendment ever came: the caller asserts on that
    raise AssertionError(f"no {action} event was recorded within {timeout_ms} ms")


def test_the_final_message_of_a_long_action_is_recorded_as_value_free_words(page: Page) -> None:
    _install_bridge(page, _UPLOAD_TRAY_HTML)
    page.click("#upload")
    event = _wait_for_event(page, "click", watch=True)

    watch = event.get("post_watch")
    assert watch is not None, "an upload that showed a progress window must leave a post_watch"
    assert watch["progress_seen"] is True
    words = [w for item in watch["final_status"] for w in item["words"]]
    assert "upload" in words and "complete" in words, watch
    assert "uploading" not in words, "the progress text is not the final message"
    assert not any(ch.isdigit() for w in words for ch in w), f"a value leaked: {words}"
    assert watch["waited_ms"] >= 800, "it must have waited for the work to finish"


def test_only_the_surface_that_showed_the_progress_speaks_for_the_result(page: Page) -> None:
    # A tray shows the work; elsewhere the page announces a window change and a tooltip appears. Both are live
    # regions that are new when the work ends, but neither is what the app said about the work.
    _install_bridge(
        page,
        """<button id="upload" onclick="
          const tray = document.createElement('div');
          tray.setAttribute('role', 'dialog');
          tray.innerHTML = '<p>Uploading 1 item</p><div role=progressbar style=width:80px;height:6px></div>';
          document.body.appendChild(tray);
          setTimeout(() => {
            const live = document.createElement('div');
            live.setAttribute('role', 'status');
            live.textContent = 'Window maximized';
            document.body.appendChild(live);
            const tip = document.createElement('div');
            tip.setAttribute('aria-live', 'polite');
            tip.textContent = 'More actions';
            document.body.appendChild(tip);
          }, 300);
          setTimeout(() => { tray.innerHTML = '<p>1 upload complete</p>'; }, 900);">Upload</button>""",
    )
    page.click("#upload")
    watch = _wait_for_event(page, "click", watch=True).get("post_watch")

    assert watch is not None
    words = [w for item in watch["final_status"] for w in item["words"]]
    assert "complete" in words, watch
    assert "maximized" not in words and "actions" not in words, f"text from another surface leaked: {words}"


def test_a_commit_click_that_shows_no_progress_records_no_post_watch_and_is_not_delayed(page: Page) -> None:
    _install_bridge(page, '<button id="save">Save</button>')
    page.click("#save")
    page.wait_for_timeout(700)

    events = _action_events(page, "click")
    assert events, "an ordinary Save must still be reported within the usual settle"
    assert events[-1].get("post_watch") is None


def test_a_chosen_file_is_watched_too(page: Page, tmp_path) -> None:
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1.4 test")
    _install_bridge(
        page,
        """<input id="file" type="file" onchange="
          const tray = document.createElement('div');
          tray.setAttribute('role', 'dialog');
          tray.textContent = 'Uploading 1 item';
          document.body.appendChild(tray);
          setTimeout(() => { tray.textContent = '1 upload complete'; }, 700);">""",
    )
    page.set_input_files("#file", str(f))
    event = _wait_for_event(page, "upload_intent", watch=True)

    watch = event.get("post_watch")
    assert watch is not None and watch["progress_seen"] is True
    assert any("complete" in item["words"] for item in watch["final_status"]), watch


def test_a_download_link_keeps_the_page_one_bridge_and_its_later_watches(page: Page) -> None:
    """WF-5 Part B: a download link fires beforeunload but the page stays. The bridge lowered its flag there, the
    recorder's check then installed a second bridge, and the next click was recorded twice; the leave also cut every
    later progress watch on the page."""
    page.route("**/files.zip", lambda route: route.fulfill(
        body=b"PK", headers={"Content-Type": "application/zip", "Content-Disposition": "attachment; filename=files.zip"}))
    _install_bridge(page, _UPLOAD_TRAY_HTML + '<a id="dl" href="http://wf5.test/files.zip">Download files</a>')
    with page.expect_download():
        page.click("#dl")
    page.wait_for_timeout(200)

    assert page.evaluate("() => window.__SKILL_BRIDGE_V1__ === true"), "the page still has its bridge"
    page.click("#upload")
    watch = _wait_for_event(page, "click", watch=True).get("post_watch")
    assert watch is not None and not watch.get("cut_by_stop"), watch
    assert any("complete" in item["words"] for item in watch["final_status"]), watch


def test_the_progress_words_of_a_non_english_app_are_recorded_in_its_own_language(page: Page) -> None:
    _install_bridge(
        page,
        """<button id="upload" onclick="
          const tray = document.createElement('div');
          tray.setAttribute('role', 'dialog');
          tray.innerHTML = '<p>Datei wird hochgeladen</p><div role=progressbar style=width:80px;height:6px></div>';
          document.body.appendChild(tray);
          setTimeout(() => { tray.innerHTML = '<p>Upload abgeschlossen</p>'; }, 800);">Upload</button>""",
    )
    page.click("#upload")
    watch = _wait_for_event(page, "click", watch=True).get("post_watch")

    assert watch is not None
    assert "hochgeladen" in watch["progress_words"], watch
    final = [w for item in watch["final_status"] for w in item["words"]]
    assert "abgeschlossen" in final and "hochgeladen" not in final, "the progress line is not the final message"


# ── BUILD-80: a step leaves the page the moment the person acts ─────────────────────────────────

# An Upload button whose tray never finishes (the page keeps showing progress), and a second button to press
# straight afterwards. Before BUILD-80 the second press waited behind the first one's watch for ~15 s.
_NEVER_ENDING_TRAY_HTML = """
<button id="upload" onclick="
  const tray = document.createElement('div');
  tray.setAttribute('role', 'dialog');
  tray.innerHTML = '<p>Uploading 1 item</p><div role=progressbar style=width:80px;height:6px></div>';
  document.body.appendChild(tray);">Upload</button>
<button id="save">Save</button>
<button id="next">Next</button>
<p id="plain">just some text</p>
"""


def _click_rows(page: Page) -> list[dict]:
    return [e for e in _events(page) if e["action"]["action"] == "click"]


def test_a_step_is_not_held_back_by_the_watch_of_an_earlier_one(page: Page) -> None:
    _install_bridge(page, _NEVER_ENDING_TRAY_HTML)
    page.click("#upload")
    page.click("#next")
    page.wait_for_timeout(500)   # far less than the 15 s watch cap

    rows = _click_rows(page)
    assert [r["target"]["id"] for r in rows] == ["upload", "next"]
    assert rows[0]["seq"] < rows[1]["seq"] and rows[0]["bridge"] == rows[1]["bridge"]
    assert page.evaluate("() => window.__skillState().open") == 1   # the Upload watch is still running, concurrently
    # The row itself carries no after-the-fact details: those arrive as amendments.
    first_raw = next(r for r in page.evaluate("() => window.__raw") if r.get("target", {}).get("id") == "upload")
    assert "post_watch" not in first_raw and first_raw["state_change"]["after"] == ""


def test_a_click_made_while_a_tray_is_already_showing_is_not_blamed_for_it(page: Page) -> None:
    _install_bridge(page, _NEVER_ENDING_TRAY_HTML)
    page.click("#upload")
    page.click("#save")           # a commit word, but the tray was already there when it was pressed
    page.wait_for_timeout(500)
    page.evaluate("() => window.__skillFlush()")
    page.wait_for_function("() => window.__skillState().open === 0", timeout=3000)

    upload, save = _click_rows(page)
    assert upload["post_watch"]["progress_seen"] is True
    assert "post_watch" not in save


def test_stop_cuts_an_open_watch_and_it_claims_no_done_words(page: Page) -> None:
    _install_bridge(page, _NEVER_ENDING_TRAY_HTML)
    page.click("#upload")
    page.wait_for_timeout(400)

    state = page.evaluate("() => window.__skillFlush()")
    assert state["open"] == 1 and state["last_seq"] >= 1
    page.wait_for_function("() => window.__skillState().open === 0", timeout=3000)

    watch = _click_rows(page)[0]["post_watch"]
    assert watch["cut_by_stop"] is True and watch["final_status"] == [] and watch["progress_seen"] is True


def test_a_real_click_on_something_that_is_not_a_control_is_counted_not_recorded(page: Page) -> None:
    _install_bridge(page, _NEVER_ENDING_TRAY_HTML)
    page.click("#plain")
    page.wait_for_timeout(200)

    assert _click_rows(page) == []
    assert [g["_gap"]["reason"] for g in page.evaluate("() => window.__gaps")] == ["no_target"]


def test_flush_sends_typing_that_was_still_waiting(page: Page) -> None:
    _install_bridge(page, '<input id="email" aria-label="Email" />', profile={"input_debounce_ms": 5000})
    page.click("#email")
    page.keyboard.type("a@b.co")

    assert _type_events(page) == []
    page.evaluate("() => window.__skillFlush()")
    page.wait_for_timeout(100)
    assert [e["action"]["value"] for e in _type_events(page)] == ["a@b.co"]



def _label_of(page: Page, selector: str) -> str | None:
    page.click(selector)
    page.keyboard.type("x")
    page.wait_for_timeout(300)
    return _type_events(page)[0]["target"]["label_text"]


def test_label_read_from_aria_labelledby_with_several_ids(page: Page) -> None:
    _install_bridge(page, '<span id="a">Work</span> <span id="b">email</span><input id="f" aria-labelledby="a b">')
    assert _label_of(page, "#f") == "Work email"


def test_label_drawn_after_the_box_is_read_from_the_next_sibling(page: Page) -> None:
    _install_bridge(page, '<div><input id="f"><span>Username</span></div>')
    assert _label_of(page, "#f") == "Username"


def test_earlier_caption_wins_over_a_later_sibling(page: Page) -> None:
    _install_bridge(page, '<div><span>First name</span><input id="f"><span>Help text here</span></div>')
    assert _label_of(page, "#f") == "First name"


def test_aria_describedby_is_the_last_resort_for_a_label(page: Page) -> None:
    _install_bridge(page, '<div><input id="f" aria-describedby="d"></div><p id="d">Your login name</p>')
    assert _label_of(page, "#f") == "Your login name"


def test_a_caption_rendered_after_the_step_is_added_when_the_page_settles(page: Page) -> None:
    # The caption does not exist when the person types; the app draws it a moment later (late-rendered form).
    _install_bridge(page, '<div id="box"><input id="f"></div>')
    page.click("#f")
    page.evaluate("() => setTimeout(() => { const s = document.createElement('span'); s.textContent = 'Username'; document.getElementById('box').prepend(s); }, 60)")
    page.keyboard.type("x")
    page.wait_for_timeout(900)
    assert _type_events(page)[0]["target"]["label_text"] == "Username"


def test_a_keyboard_pick_in_a_native_list_is_one_select_step(page: Page) -> None:
    """Focusing a list and pressing the down arrow changes its value. That is one pick: recording the arrow and the
    list's value as typing as well made four steps, and a replayed arrow moved the choice again (WF-3 Leg B)."""
    _install_bridge(page, """
      <select id="sort" aria-label="Sort products"><option value="az">Name (A to Z)</option>
        <option value="za">Name (Z to A)</option></select><button id="next">Next</button>""")
    page.focus("#sort")
    page.keyboard.press("ArrowDown")
    page.wait_for_timeout(120)
    page.click("#next")
    page.wait_for_timeout(120)
    kinds = [(e["action"]["action"], e["target"].get("id")) for e in _events(page)]
    on_list = [k for k in kinds if k[1] == "sort"]
    assert on_list == [("select", "sort")], kinds
    assert _action_events(page, "select")[0]["action"]["value"] == "za"


def test_a_button_input_is_not_named_after_hidden_page_text(page: Page) -> None:
    """A submit <input> is named by its own value, and a <noscript> above the form is never anyone's caption."""
    _install_bridge(page, """
      <noscript>You need to enable JavaScript to run this app.</noscript>
      <div><form onsubmit="event.preventDefault()"><div><input id="first" placeholder="First Name"></div>
        <div><input id="go" type="submit" value="Continue"></div></form></div>""")
    page.click("#go")
    page.click("#first")
    page.keyboard.type("Ada")
    page.wait_for_timeout(150)
    labels = [e["target"].get("label_text") for e in _events(page)]
    assert all("JavaScript" not in str(lab or "") for lab in labels), labels


# WF-3 D (ParaBank Transfer Funds): a form of bare text captions under a page banner.
_TRANSFER_FORM = """
<div id="topPanel"><p class="caption">Experience the difference</p></div>
<div><h1>Transfer Funds</h1>
  <form id="transferForm" onsubmit="return false">
    <p><b>Amount:</b> $<input id="amount" type="text" name="input"></p>
    <div>
      From account #
      <select id="fromAccountId" class="input"><option value="1">12345</option><option value="2">12456</option></select>
      to account #
      <select id="toAccountId" class="input"><option value="1">12345</option><option value="2">12456</option></select>
    </div>
    <div><input type="submit" class="button" value="Transfer"></div>
    <div><select id="memo" class="input"><option value="a">A</option><option value="b">B</option></select></div>
  </form>
</div>
"""


def test_a_caption_written_as_bare_text_names_the_dropdown_after_it(page: Page) -> None:
    _install_bridge(page, _TRANSFER_FORM)
    page.select_option("#toAccountId", "2")
    page.wait_for_timeout(80)
    assert _action_events(page, "select")[0]["target"]["label_text"] == "to account #"


def test_a_dropdown_with_no_caption_in_its_form_is_not_named_after_the_page_banner(page: Page) -> None:
    _install_bridge(page, _TRANSFER_FORM)
    page.select_option("#memo", "b")
    page.wait_for_timeout(80)
    assert _action_events(page, "select")[0]["target"]["label_text"] in (None, "")


def test_a_radio_is_not_named_after_the_option_before_it(page: Page) -> None:
    _install_bridge(page, '<form><input type="radio" name="g" id="m"> Male <input type="radio" name="g" id="f"> Female</form>')
    page.click("#f")
    page.wait_for_timeout(80)
    assert _events(page)[-1]["target"]["label_text"] != "Male"


def test_suggestions_that_close_are_not_recorded_as_new_words(page: Page) -> None:
    """WF-4 Leg C: Enter closed Wikipedia's suggestion list; its hidden links read as glued new words
    ("T-shirt (Redirected from Tshirt)Style of …"), offered as "how we know it worked" and failing every run."""
    _install_bridge(page, """
      <input id="q" aria-label="Search">
      <div id="s"><a href="#a"><span>T-shirt</span><br><span>Style of shirt</span></a></div>
      <script>document.getElementById("q").addEventListener("keydown", e => {
        if (e.key === "Enter") document.getElementById("s").style.display = "none"; });</script>""")
    page.click("#q")
    page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    enter = next(e for e in _events(page) if e["action"]["action"] == "keyboard_shortcut")
    diff = (enter.get("state_change") or {}).get("dom_diff") or {}
    assert not any("Style" in line for line in diff.get("added") or []), diff
    assert any(line.endswith("T-shirt Style of shirt") for line in diff.get("removed") or []), diff


def test_a_suggestion_picked_on_press_and_removed_is_still_recorded_as_its_click(page: Page) -> None:
    """WF-4 Leg A (OrangeHRM employee box): the list picks on press, fills the box and removes the option before the
    button is released, so no click event fires. The pick was lost, and the box's fill was saved as typing."""
    _install_bridge(page, """
      <input id="emp" aria-label="Employee"><div id="list"></div><button id="other">Other</button>
      <script>
        const box = document.getElementById("emp"), list = document.getElementById("list");
        box.addEventListener("input", () => { list.innerHTML = '<div role="option" id="opt">Amelia Brown</div>'; });
        list.addEventListener("mousedown", (e) => {
          if (e.target.id !== "opt") return;
          e.preventDefault(); box.value = "Amelia  Brown"; box.blur(); list.innerHTML = "";
        });
      </script>""")
    page.click("#emp")
    page.keyboard.type("Amelia")
    page.wait_for_timeout(80)
    box = page.locator("#opt").bounding_box()
    page.mouse.click(box["x"] + 5, box["y"] + 5)
    page.wait_for_timeout(200)
    rows = [(e["action"]["action"], e["action"].get("value"), e["target"].get("inner_text") or e["target"].get("id"))
            for e in _events(page)]
    assert ("type", "Amelia", "emp") in rows, rows
    assert not any(a == "type" and v == "Amelia  Brown" for a, v, _ in rows), rows
    assert any(a == "click" and "Amelia Brown" in str(t) for a, _v, t in rows), rows


def test_a_press_or_drag_on_a_canvas_is_recorded_as_an_unsupported_gesture_not_a_click(page: Page) -> None:
    """WF-8 8.12: drawing on Excalidraw's canvas became plain clicks on the board around it, and the workflow 'drew a
    rectangle' by clicking nothing."""
    _install_bridge(
        page,
        '<div id="board" role="application" aria-label="Board"><canvas id="c" width="300" height="200"></canvas></div>'
        '<button id="ok">OK</button>',
    )
    page.mouse.move(50, 50)
    page.mouse.down()
    page.mouse.move(120, 110, steps=4)
    page.mouse.up()                      # a drag
    page.click("#c", position={"x": 20, "y": 20})   # a plain press
    page.click("#ok")
    page.wait_for_timeout(60)

    events = _events(page)
    assert [e["action"]["action"] for e in events] == ["unsupported_gesture", "unsupported_gesture", "click"]
    assert [json.loads(e["action"]["value"])["moved"] for e in events[:2]] == [True, False]
    assert events[0]["target"]["tag"] == "canvas"
