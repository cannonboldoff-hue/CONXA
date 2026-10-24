"""Conversation command handlers (BUILD-37): chats, workflow threads, cards and lifecycle.

Thin on purpose — the turn loop is ``conxa_compile/conversation/harness.py``, storage is
``thread_store.py``, state is ``lifecycle.py``. This mixin only wires them to the Backend: it
installs the metered LLM router, builds the per-turn callbacks (digest, gates, card actions),
and turns the proxy's entitlement/quota errors into error cards in the thread.

Every change a card can make goes through a command that already exists
(``cmd_accept_copilot_proposal``, ``cmd_undo_workflow``, ``cmd_create_workflow``) — the thread
never gets a second way to edit a workflow.
"""

from __future__ import annotations

from typing import Any

from handlers.protocol import _CommandError, _event_sink, _safe_id

_THREAD_PAGE = 40


def _thread_id(payload: dict[str, Any]) -> str:
    from conxa_compile.conversation import thread_store as ts

    thread_id = str(payload.get("thread_id") or "").strip()
    try:
        ts.parse_thread_id(thread_id)
    except ts.ThreadIdError as exc:
        raise _CommandError("invalid_input", str(exc)) from exc
    return thread_id


class ConversationMixin:
    # ── general chats ───────────────────────────────────────────────────────────────────────

    def cmd_chat_list(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import thread_store as ts

        return {"chats": ts.list_chats()}

    def cmd_chat_create(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import thread_store as ts

        return {"chat": ts.create_chat(str(payload.get("title") or "New chat"))}

    def cmd_chat_rename(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import thread_store as ts

        chat = ts.rename_chat(_safe_id(payload.get("chat_id"), "chat_id"), str(payload.get("title") or ""))
        if chat is None:
            raise _CommandError("chat_not_found", "No such chat, or the name was empty.")
        return {"chat": chat}

    def cmd_chat_delete(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import thread_store as ts

        return {"deleted": ts.delete_chat(_safe_id(payload.get("chat_id"), "chat_id"))}

    # ── lifecycle ───────────────────────────────────────────────────────────────────────────

    def cmd_get_lifecycle(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation.lifecycle import lifecycle_for_workflow
        from conxa_core.storage.workflow_store import get_workflow

        workflow = get_workflow(_safe_id(payload.get("workflow_id"), "workflow_id"))
        if workflow is None:
            raise _CommandError("workflow_not_found", "No such workflow.")
        return {"lifecycle": lifecycle_for_workflow(workflow)}

    def cmd_list_lifecycles(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Every workflow's lifecycle in one call — what the sidebar's glyphs read."""
        from conxa_compile.conversation.lifecycle import lifecycle_for_workflow
        from conxa_core.storage.workflow_store import list_workflows

        out: dict[str, Any] = {}
        for wf in list_workflows():
            lc = lifecycle_for_workflow(wf)
            out[wf.id] = {k: lc[k] for k in ("stage", "substate", "glyph", "note", "allowed_actions")}
        return {"lifecycles": out}

    def cmd_count_publishable(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """Workflows a person has approved — what the sidebar's Publish badge counts."""
        from conxa_core.storage.workflow_store import list_workflows

        return {"count": sum(1 for wf in list_workflows() if wf.signed_off)}

    # ── threads ─────────────────────────────────────────────────────────────────────────────

    def cmd_thread_load(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation import thread_store as ts
        from conxa_compile.conversation.lifecycle import lifecycle_for_workflow
        from conxa_core.storage.group_store import get_group
        from conxa_core.storage.workflow_store import get_workflow

        thread_id = _thread_id(payload)
        kind, ident = ts.parse_thread_id(thread_id)
        before = payload.get("before")
        page = ts.load(thread_id, before=int(before) if before is not None else None, limit=int(payload.get("limit") or _THREAD_PAGE))
        if kind == "chat":
            chat = next((c for c in ts.list_chats() if c.get("id") == ident), None)
            return {**page, "thread_id": thread_id, "chat": chat}

        workflow = get_workflow(ident)
        if workflow is None:
            raise _CommandError("workflow_not_found", "No such workflow.")
        self._reconcile_interrupted(ident)
        page = ts.load(thread_id, before=int(before) if before is not None else None, limit=int(payload.get("limit") or _THREAD_PAGE))
        if before is None:
            # Read-only, once per skill: the reviewer's old copilot history joins the thread.
            if ts.import_copilot_session(ident, workflow.skill_id):
                page = ts.load(thread_id, limit=int(payload.get("limit") or _THREAD_PAGE))
        group = get_group(workflow.group_id) if workflow.group_id else None
        return {
            **page,
            "thread_id": thread_id,
            "workflow": {"id": workflow.id, "name": workflow.name, "group_id": workflow.group_id,
                         "group_name": group.name if group else "", "skill_id": workflow.skill_id,
                         "target_url": workflow.target_url},
            "lifecycle": lifecycle_for_workflow(workflow),
        }

    def cmd_thread_cancel(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        from conxa_compile.conversation.harness import cancel_turn

        return {"cancelled": cancel_turn(str(payload.get("turn_id") or ""))}

    def cmd_thread_turn(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """One conversation turn: a free-text message, or an answer to a card (``card_id`` plus
        ``choice``/``action``). Streams ``thread_delta`` events and appends ``thread_item``s."""
        from conxa_compile.conversation import harness
        from conxa_compile.conversation import thread_store as ts
        from services.llm_proxy_client import CloudUnreachable, EntitlementBlocked, ProxyUnavailable, QuotaExceeded

        thread_id = _thread_id(payload)
        kind, ident = ts.parse_thread_id(thread_id)
        sink = _event_sink(rid)
        ctx = self._thread_context(kind, ident, sink)
        self._install_proxy_router(sink=sink, usage_class="ai_credits")
        if kind == "chat":
            # A new chat is named after its first message, then left alone.
            chat = next((c for c in ts.list_chats() if c.get("id") == ident), None)
            text = " ".join(str(payload.get("message") or "").split())
            if chat is not None and chat.get("title") == "New chat" and text:
                ts.touch_chat(ident, title=text[:40] + ("…" if len(text) > 40 else ""))
            elif chat is not None:
                ts.touch_chat(ident)

        def execute(card: dict[str, Any], action: str, body: dict[str, Any]) -> dict[str, Any]:
            return self._execute_card_action(thread_id, card, action, body, rid, sink)

        def run_action(action_id: str, slots: dict[str, Any]) -> dict[str, Any]:
            if kind != "wf":
                return {"ok": False, "message": "Open a workflow first."}
            return self._run_flow_action(ident, action_id, slots, rid, sink)

        try:
            return harness.run_turn(
                thread_id=thread_id,
                message=str(payload.get("message") or ""),
                sink=sink,
                turn_id=str(payload.get("turn_id") or "") or None,
                anchor=str(payload.get("anchor") or "") or None,
                card_id=str(payload.get("card_id") or "") or None,
                choice=str(payload.get("choice") or "") or None,
                action=str(payload.get("action") or "") or None,
                card_executor=execute,
                flow_executor=run_action,
                **ctx,
            )
        except ValueError as exc:
            raise _CommandError("invalid_input", str(exc)) from exc
        except (EntitlementBlocked, QuotaExceeded, ProxyUnavailable, CloudUnreachable) as exc:
            code, message = self._llm_error(exc)
            harness.post_card(thread_id, "error", "failed", sink=sink, code=code, title="Couldn’t get a reply",
                              body=message, actions=["retry"])
            raise _CommandError(code, message) from exc

    def cmd_card_action(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """A button on a card (Apply, Undo, Create, …) — no model involved."""
        from conxa_compile.conversation import thread_store as ts

        thread_id = _thread_id(payload)
        card_id = str(payload.get("card_id") or "").strip()
        card = ts.get_card(thread_id, card_id)
        if card is None:
            raise _CommandError("card_not_found", "That card isn’t in this conversation any more.")
        sink = _event_sink(rid)
        return self._execute_card_action(
            thread_id, card, str(payload.get("action") or ""),
            payload.get("payload") if isinstance(payload.get("payload"), dict) else {}, rid, sink,
        )

    # ── per-thread context ──────────────────────────────────────────────────────────────────

    def _llm_error(self, exc: Exception) -> tuple[str, str]:
        from services.llm_proxy_client import CloudUnreachable, EntitlementBlocked, ProxyUnavailable, QuotaExceeded

        if isinstance(exc, EntitlementBlocked):
            return exc.code, self._entitlement_error_message(exc.code)
        if isinstance(exc, QuotaExceeded):
            return "quota_exceeded", str(exc)
        if isinstance(exc, ProxyUnavailable):  # before CloudUnreachable, its parent: the cloud answered
            return "llm_no_output", str(exc)
        return "cloud_unreachable", str(exc)

    def _chat_context(self) -> dict[str, Any]:
        """What General Chat may see, read-only: groups and the apps they sign in to, the person's
        workflows with where each one stands, and which apps have a learned map. Capped, so a big
        workspace can't crowd the conversation out of the budget."""
        from conxa_compile.conversation.lifecycle import lifecycle_for_workflow
        from conxa_core.storage.group_store import list_groups
        from conxa_core.storage.workflow_store import list_workflows

        groups = list_groups()
        by_group = {g.id: g.name for g in groups}
        workflows = []
        for wf in list_workflows()[:50]:
            try:
                note = lifecycle_for_workflow(wf)["note"]
            except Exception:  # noqa: BLE001 -- a hint, never worth failing a chat turn
                note = ""
            workflows.append({"id": wf.id, "name": wf.name, "group": by_group.get(wf.group_id, ""), "note": note})
        sections: list[tuple[str, str]] = []
        apps = [f"{g.name}: " + (", ".join(a.name for a in g.apps) or "no apps yet") for g in groups[:30]]
        if apps:
            sections.append(("Apps each group signs in to (read-only)", "; ".join(apps)))
        if workflows:
            sections.append(("The person's workflows (read-only)", "\n".join(
                f"- {w['name']}" + (f" (group {w['group']})" if w["group"] else "") + (f" — {w['note']}" if w["note"] else "")
                for w in workflows)))
        try:
            from conxa_core.storage.app_graph_store import list_all_apps

            maps = [str(a.get("host") or a.get("app_key") or "") for a in list_all_apps()[:20]]
            if any(maps):
                sections.append(("Apps Studio has learned a map of", ", ".join(m for m in maps if m)))
        except Exception:  # noqa: BLE001
            pass
        return {"group_names": [g.name for g in groups], "workflows": workflows, "context_sections": sections}

    def _thread_context(self, kind: str, ident: str, sink: Any) -> dict[str, Any]:
        """The keyword arguments ``harness.run_turn`` needs that depend on what the thread is."""
        if kind == "chat":
            return self._chat_context()

        from conxa_compile.conversation import clarify_store
        from conxa_compile.conversation.lifecycle import lifecycle_for_workflow
        from conxa_core.storage.json_store import read_skill
        from conxa_core.storage.workflow_store import get_workflow

        workflow = get_workflow(ident)
        if workflow is None:
            raise _CommandError("workflow_not_found", "No such workflow.")
        skill_id = workflow.skill_id
        digest_cache: dict[str, Any] = {}

        def load_digest() -> dict[str, Any] | None:
            if not skill_id:
                return None
            if "d" not in digest_cache:
                from conxa_compile.editor.evidence import EvidenceError, build_evidence_bundle

                try:
                    digest_cache["d"] = build_evidence_bundle(skill_id, run_id=None, detail="digest")
                except EvidenceError:
                    digest_cache["d"] = None
            return digest_cache["d"]

        def gate(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
            from conxa_compile.editor.copilot_proposals import (
                gate_overlay_proposals,
                gate_proposals,
                gate_structural_proposals,
            )

            doc = read_skill(skill_id) if skill_id else None
            if doc is None:
                return []
            proposals = gate_proposals(doc, raw)
            observed = ((load_digest() or {}).get("runtime_evidence") or {}).get("observed_overlays") or []
            if observed:
                proposals = proposals + gate_overlay_proposals(doc, observed, raw)
            return proposals + gate_structural_proposals(doc, raw)

        return {
            "workflow": {"id": workflow.id, "name": workflow.name, "task_description": workflow.task_description},
            "skill_id": skill_id,
            "lifecycle": lifecycle_for_workflow(workflow),
            "load_digest": load_digest,
            "load_clarify": lambda: clarify_store.load(workflow.id),
            "gate_proposals": gate,
            "last_test_failed": workflow.last_test_status == "failed",
        }

    # ── card actions ────────────────────────────────────────────────────────────────────────

    def _execute_card_action(self, thread_id: str, card: dict[str, Any], action: str,
                             body: dict[str, Any], rid: str, sink: Any) -> dict[str, Any]:
        card_type = card.get("card_type")
        handler = getattr(self, f"_card_{card_type}", None)
        if handler is None:
            raise _CommandError("card_action_unsupported", f"Nothing to do for a {card_type} card.")
        return handler(thread_id, card, action, body, rid, sink)

    # ── conversational actions (gap analysis G2) ────────────────────────────────────────────

    def _reconcile_interrupted(self, workflow_id: str) -> None:
        """A test (or auto-fix) card that says it is still going, in a Studio that is not testing anything, was
        interrupted: the app closed or restarted, or the engine died. Say so instead of leaving a spinner that will
        never finish. Pause decisions live in memory by design, so there is nothing to resume; running it again is
        the honest next step. Cards younger than 30 s are left alone (a test that is only just starting)."""
        import time
        from datetime import datetime

        from conxa_compile.conversation import harness, runtime_state, thread_store as ts

        flags = runtime_state.get_flags(workflow_id)
        if flags.get("testing") or flags.get("autofix_attempt"):
            return
        thread = f"wf:{workflow_id}"
        live = {"test_run": ("running", "waiting_guard", "waiting_confirm", "waiting_fork"), "autofix": ("running",)}
        for card in ts.fold(ts.read_raw(thread)):
            if card.get("kind") != "card" or card.get("state") not in live.get(str(card.get("card_type")), ()):
                continue
            try:
                age = time.time() - datetime.fromisoformat(str(card.get("ts")).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                age = 1e9
            if age < 30:
                continue
            harness.update_card(thread, card["card_id"], state="interrupted", actions=["test_again"])

    def _latest_card(self, workflow_id: str, card_type: str, states: tuple[str, ...]) -> dict[str, Any] | None:
        from conxa_compile.conversation import thread_store as ts

        for c in reversed(ts.fold(ts.read_raw(f"wf:{workflow_id}"))):
            if c.get("kind") == "card" and c.get("card_type") == card_type and c.get("state") in states:
                return c
        return None

    def _require_allowed(self, workflow_id: str, action: str) -> None:
        """The lifecycle's ``allowed_actions`` is the one rule for what may start now. The strip and
        chat routing read it, but a stale confirm card, a summary-card button or a direct command
        would not, so every entry point re-checks it here."""
        from conxa_compile.conversation.lifecycle import lifecycle_for_workflow
        from conxa_core.storage.workflow_store import get_workflow

        workflow = get_workflow(workflow_id)
        if workflow is None:
            return  # the action's own command reports a missing workflow
        lc = lifecycle_for_workflow(workflow)
        if action not in lc["allowed_actions"]:
            raise _CommandError("not_allowed_now", f"Not yet — {lc['note'][:1].lower()}{lc['note'][1:]}.")

    def _run_flow_action(self, workflow_id: str, action: str, slots: dict[str, Any], rid: str, sink: Any) -> dict[str, Any]:
        """Run one of conversation/actions.ACTIONS through the same ``cmd_flow_*`` commands the card
        buttons use. Returns ``{"ok", "message"}`` and never raises, so a failed action becomes a
        sentence in the thread rather than a broken turn."""
        try:
            return self._run_flow_action_inner(workflow_id, action, slots or {}, rid, sink)
        except _CommandError as exc:
            return {"ok": False, "message": exc.message or exc.code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}

    def _run_flow_action_inner(self, workflow_id: str, action: str, slots: dict[str, Any], rid: str, sink: Any) -> dict[str, Any]:
        from conxa_compile.conversation import harness

        base = {"workflow_id": workflow_id}
        self._require_allowed(workflow_id, action)

        def need(card_type: str, states: tuple[str, ...], what: str) -> dict[str, Any]:
            card = self._latest_card(workflow_id, card_type, states)
            if card is None:
                raise _CommandError("nothing_to_do", f"There is no {what} to act on right now.")
            return {**base, "card_id": card["card_id"]}

        if action == "sign_in":
            from conxa_core.storage.workflow_store import get_workflow

            wf = get_workflow(workflow_id)
            if wf is not None and wf.skill_id:     # a compiled workflow: sign in, then carry on testing
                res = self.cmd_flow_sign_in_then_test(base, rid)
                return {"ok": True, "message": "Opening the sign-in page; I\u2019ll test again once you\u2019re in." if res.get("next") == "sign_in"
                        else "Testing again."}
        if action in ("record", "sign_in", "re_record"):
            res = self.cmd_flow_begin(base, rid)
            return {"ok": True, "message": "That’s already going — look at the card above." if res.get("already") else
                    ("Opening the sign-in page first." if res.get("next") == "sign_in" else "Opening the browser to record.")}
        if action == "finish_recording":
            self.cmd_flow_finish_recording(need("recording", ("recording", "paused"), "recording"), rid)
            return {"ok": True, "message": "Saving the recording."}
        if action == "discard_recording":
            self.cmd_flow_discard_recording(need("recording", ("recording", "paused"), "recording"), rid)
            return {"ok": True, "message": "Discarded the recording."}
        if action == "finish_sign_in":
            self.cmd_flow_sign_in_done(need("sign_in", ("waiting",), "sign-in"), rid)
            return {"ok": True, "message": "Checking the sign-in."}
        if action == "cancel":
            self.cmd_flow_sign_in_cancel(need("sign_in", ("waiting",), "sign-in"), rid)
            return {"ok": True, "message": "Cancelled the sign-in."}
        if action == "compile":
            self._flow_start_compile(workflow_id)
            return {"ok": True, "message": "Compiling the recording."}
        if action == "answer_clarify":
            self._flow_post_next_question(workflow_id, sink, promote_queued=True)
            return {"ok": True, "message": "Here’s the next question."}
        if action == "test":
            prepared = self.cmd_flow_test_prepare(base, rid)
            stated = (slots.get("inputs") or {}) if isinstance(slots.get("inputs"), dict) else {}
            if stated:
                from conxa_compile.conversation import thread_store as ts

                card = ts.get_card(f"wf:{workflow_id}", prepared["card_id"]) or {}
                rows = [{**i, "value": stated.get(i["id"], i.get("value", ""))} for i in card.get("inputs") or []]
                harness.update_card(f"wf:{workflow_id}", prepared["card_id"], sink=sink, inputs=rows)
            return {"ok": True, "message": "Check the test inputs on the card, then press Run — nothing has run yet."}
        if action == "stop_test":
            self.cmd_flow_test_stop(base, rid)
            return {"ok": True, "message": "Stopping the test."}
        if action in ("autofix", "resume_autofix"):
            self.cmd_flow_autofix_resume(base, rid)
            return {"ok": True, "message": "Looking for a fix."}
        if action == "approve":
            self.cmd_flow_approve(need("test_run", ("passed",), "passing test"), rid)
            return {"ok": True, "message": "Approved."}
        raise _CommandError("action_unsupported", "I can’t do that from here yet.")

    def _card_confirm_action(self, thread_id: str, card: dict[str, Any], action: str,
                             body: dict[str, Any], rid: str, sink: Any) -> dict[str, Any]:
        from conxa_compile.conversation import harness

        if card.get("state") != "open":
            return {"ok": False, "code": "not_open", "message": "That was already handled."}
        if action == "cancel":
            harness.update_card(thread_id, card["card_id"], sink=sink, state="dismissed", actions=[])
            return {"ok": True}
        if action != "confirm":
            raise _CommandError("card_action_unsupported", f"A confirmation can’t “{action}”.")
        harness.update_card(thread_id, card["card_id"], sink=sink, state="running", actions=[])
        from conxa_compile.conversation import thread_store as ts

        _kind, ident = ts.parse_thread_id(thread_id)
        result = self._run_flow_action(ident, str(card.get("action") or ""), card.get("slots") or {}, rid, sink)
        harness.update_card(thread_id, card["card_id"], sink=sink, state="done" if result.get("ok") else "failed",
                            receipt=str(result.get("message") or ""), actions=[])
        if not result.get("ok"):
            return {"ok": False, "code": "action_failed", "message": result.get("message")}
        return {"ok": True, "message": result.get("message")}

    def cmd_flow_do_action(self, payload: dict[str, Any], rid: str) -> dict[str, Any]:
        """The action strip's buttons: the same table and the same commands chat uses, so a button
        and a typed "test it" can never drift apart. The press itself is the confirmation."""
        from conxa_compile.conversation.actions import ACTIONS

        workflow_id = _safe_id(payload.get("workflow_id"), "workflow_id")
        action = str(payload.get("action") or "")
        spec = ACTIONS.get(action)
        if spec is None or spec.kind != "flow":
            raise _CommandError("action_unsupported", "That isn’t something I can run from here.")
        slots = payload.get("slots") if isinstance(payload.get("slots"), dict) else {}
        return self._run_flow_action(workflow_id, action, slots, rid, _event_sink(rid))

    def _card_action_link(self, thread_id: str, card: dict[str, Any], action: str,
                          body: dict[str, Any], rid: str, sink: Any) -> dict[str, Any]:
        """Opening something is the renderer's job; the card only records that it was used."""
        from conxa_compile.conversation import harness

        harness.update_card(thread_id, card["card_id"], sink=sink, state="opened", actions=[])
        return {"ok": True}

    def _log_edit(self, cmd: str, skill_id: str, before_doc: Any, *, source: str, proposal_id: str | None) -> None:
        """The dispatch hook's edit-log write, for edits made by a card rather than a top-level
        command (dispatch never sees those, so they'd otherwise leave no trace)."""
        try:
            from conxa_compile.editor.edit_log import append_edit
            from conxa_core.storage.json_store import read_skill

            append_edit(skill_id, cmd, before_doc, read_skill(skill_id), source=source, proposal_id=proposal_id)
        except Exception:  # noqa: BLE001 — the log must never block an edit
            pass

    def _card_pending_change(self, thread_id: str, card: dict[str, Any], action: str,
                             body: dict[str, Any], rid: str, sink: Any) -> dict[str, Any]:
        from conxa_compile.conversation import harness
        from conxa_core.storage.json_store import read_skill

        card_id = card["card_id"]
        skill_id = str((card.get("refs") or {}).get("skill_id") or "")
        # One card = one reply's change set; a card from before change sets carries one `proposal`.
        proposals = [p for p in card.get("proposals") or [] if isinstance(p, dict)]
        if not proposals and isinstance(card.get("proposal"), dict):
            proposals = [card["proposal"]]
        proposal = proposals[0] if proposals else {}
        title = card.get("title") or "the change"

        if action == "dismiss":
            if card.get("state") != "open":
                return {"ok": False, "code": "not_open"}
            for p in proposals:
                try:
                    self.cmd_reject_copilot_proposal({**p, "skill_id": skill_id, "proposal_id": p.get("id")}, rid)
                except _CommandError:
                    pass  # a dismissal that can't be logged is still a dismissal
            harness.update_card(thread_id, card_id, state="dismissed", sink=sink)
            return {"ok": True}

        if action == "apply":
            if card.get("state") != "open":
                return {"ok": False, "code": "not_open", "message": "This change was already handled."}
            source = str(body.get("source") or "copilot")
            harness.update_card(thread_id, card_id, state="applying", sink=sink)
            before_stack = len(self._undo_stacks.get(skill_id, []))
            before_doc = read_skill(skill_id)
            try:
                self._apply_change_set(skill_id, proposals, rid)
            except _CommandError as exc:
                harness.update_card(thread_id, card_id, state="failed", error=exc.message, sink=sink)
                return {"ok": False, "code": exc.code, "message": exc.message}
            self._log_edit("accept_copilot_proposal", skill_id, before_doc, source=source, proposal_id=proposal.get("id"))
            after = read_skill(skill_id) or {}
            harness.update_card(
                thread_id, card_id, state="applied", sink=sink,
                undo_steps=max(1, len(self._undo_stacks.get(skill_id, [])) - before_stack),
                applied_version=(after.get("meta") or {}).get("version"),
                actions=["undo"],
            )
            harness.log_event(thread_id, f"Applied: {title}", glyph="ok", sink=sink)
            return {"ok": True}

        if action == "undo":
            if card.get("state") != "applied":
                return {"ok": False, "code": "not_applied"}
            current = read_skill(skill_id) or {}
            if (current.get("meta") or {}).get("version") != card.get("applied_version"):
                # Something else changed the workflow since — undoing blindly could throw that
                # away too, so ask the person to look instead.
                return {"ok": False, "code": "changed_since",
                        "message": "The workflow changed after this edit. Open Human Edit to undo it there."}
            before_doc = current
            for _ in range(int(card.get("undo_steps") or 1)):
                self.cmd_undo_workflow({"skill_id": skill_id}, rid)
            self._log_edit("undo_workflow", skill_id, before_doc, source="human", proposal_id=proposal.get("id"))
            harness.update_card(thread_id, card_id, state="undone", actions=[], sink=sink)
            harness.log_event(thread_id, f"Undid: {title}", glyph="none", sink=sink)
            return {"ok": True}

        raise _CommandError("card_action_unsupported", f"A change card can’t “{action}”.")

    def _card_draft_workflow(self, thread_id: str, card: dict[str, Any], action: str,
                             body: dict[str, Any], rid: str, sink: Any) -> dict[str, Any]:
        from conxa_compile.conversation import harness
        from conxa_core.storage.group_store import list_groups

        card_id = card["card_id"]
        if action == "dismiss":
            harness.update_card(thread_id, card_id, state="dismissed", actions=[], sink=sink)
            return {"ok": True}
        if action == "edit_details":
            # Nothing is created: the renderer opens the New workflow form prefilled from the draft.
            return {"ok": True, "open_form": card.get("draft") or {}}
        if action not in ("create", "create_and_record"):
            raise _CommandError("card_action_unsupported", f"A draft can’t “{action}”.")
        if card.get("state") != "open":
            return {"ok": False, "code": "not_open", "message": "This draft was already used."}

        draft = {**(card.get("draft") or {}), **{k: v for k, v in body.items() if k in ("name", "group", "starting_page", "task") and v}}
        group_id = str(body.get("group_id") or "")
        if not group_id:
            match = next((g for g in list_groups() if g.name == draft.get("group")), None)
            group_id = match.id if match else ""
        missing = [n for n, v in (("name", draft.get("name")), ("group", group_id), ("starting_page", draft.get("starting_page"))) if not v]
        if missing:
            return {"ok": False, "code": "needs_details", "needs": missing, "open_form": draft}
        created = self.cmd_create_workflow(
            {"name": draft["name"], "group_id": group_id, "target_url": draft["starting_page"], "task_description": draft.get("task") or ""},
            rid,
        )
        wf = created["workflow"]
        wf_id = wf["id"] if isinstance(wf, dict) else wf.id
        harness.update_card(thread_id, card_id, state="created", refs={"workflow_id": wf_id}, actions=[], sink=sink)
        return {"ok": True, "workflow_id": wf_id, "start_recording": action == "create_and_record"}

    def _card_error(self, thread_id: str, card: dict[str, Any], action: str,
                    body: dict[str, Any], rid: str, sink: Any) -> dict[str, Any]:
        from conxa_compile.conversation import harness

        if action == "retry":
            harness.update_card(thread_id, card["card_id"], state="dismissed", actions=[], sink=sink)
            return {"ok": True, "retry": True}
        raise _CommandError("card_action_unsupported", f"An error card can’t “{action}”.")
