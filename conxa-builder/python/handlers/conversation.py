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

    def _thread_context(self, kind: str, ident: str, sink: Any) -> dict[str, Any]:
        """The keyword arguments ``harness.run_turn`` needs that depend on what the thread is."""
        if kind == "chat":
            from conxa_core.storage.group_store import list_groups

            return {"group_names": [g.name for g in list_groups()]}

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
            "workflow": {"id": workflow.id, "name": workflow.name},
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
        proposal = card.get("proposal") if isinstance(card.get("proposal"), dict) else {}
        title = card.get("title") or "the change"

        if action == "dismiss":
            if card.get("state") != "open":
                return {"ok": False, "code": "not_open"}
            try:
                self.cmd_reject_copilot_proposal({**proposal, "skill_id": skill_id, "proposal_id": proposal.get("id")}, rid)
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
                self.cmd_accept_copilot_proposal({**proposal, "skill_id": skill_id, "proposal_id": proposal.get("id")}, rid)
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
