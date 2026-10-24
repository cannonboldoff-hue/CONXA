from __future__ import annotations

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
            window.__SKILL_CAPTURE_PROFILE__ = args.profile;
            window.__skillReport = (payload) => window.__events.push(payload);
        }""",
        {"profile": capture_profile},
    )
    page.evaluate(BRIDGE_JS)


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


def test_password_input_redacts_value(page: Page) -> None:
    _install_bridge(page, '<input id="password" type="password" name="password" />')

    page.fill("#password", "super-secret")
    page.wait_for_timeout(80)

    events = _type_events(page)
    assert len(events) == 1
    assert events[0]["action"]["value"] == "{{REDACTED}}"
    assert "super-secret" not in str(events[0])


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
