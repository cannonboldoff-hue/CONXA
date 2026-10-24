"""The one table of things a person can ask for in conversation (gap analysis G2).

``lifecycle.derive_lifecycle`` already says which actions make sense *right now*
(``allowed_actions``). This module says what each of them is: what to call it, whether it is
something the Studio runs (``flow``) or something the renderer opens (``nav``), whether it has a
side effect that needs a confirm card first, and the plain phrases that mean exactly it.

Nothing here talks to a model or a backend command; ``intent_router`` picks one of these ids and
the handler runs it through the same ``cmd_flow_*`` commands the buttons use.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActionSpec:
    id: str
    label: str            # what the confirm card / action strip says
    kind: str             # "flow" runs a backend command, "nav" opens something, "none" is card-only
    side_effect: bool     # True: a confirm card comes first (records, spends a credit, sends data, approves)
    describe: str         # one line the router prompt shows the model
    phrases: tuple[str, ...] = ()   # whole-message phrases that mean exactly this action
    target: str = ""      # for nav: where the renderer goes


ACTIONS: dict[str, ActionSpec] = {a.id: a for a in (
    ActionSpec("edit_details", "Edit details", "nav", False, "Change the workflow's name, start page or description",
               ("edit details", "change the name", "rename it"), target="details"),
    ActionSpec("sign_in", "Sign in", "flow", True, "Sign in to the app this workflow uses",
               ("sign in", "log in", "sign in again", "login")),
    ActionSpec("finish_sign_in", "I'm signed in", "flow", False, "Tell Studio the sign-in is finished",
               ("i'm signed in", "im signed in", "signed in", "i am signed in")),
    ActionSpec("cancel", "Cancel sign-in", "flow", False, "Cancel the sign-in that is waiting",
               ("cancel", "cancel sign in", "never mind")),
    ActionSpec("record", "Start recording", "flow", True, "Open the browser and record the task",
               ("record", "start recording", "record it", "let's record", "lets record")),
    ActionSpec("finish_recording", "Finish recording", "flow", True, "Save the recording and compile it",
               ("finish recording", "stop recording", "i'm done recording", "im done recording", "finish", "done recording")),
    ActionSpec("discard_recording", "Discard recording", "flow", True, "Throw the current recording away",
               ("discard recording", "discard it", "throw it away", "start over")),
    ActionSpec("re_record", "Record again", "flow", True, "Record the task again from the start",
               ("record again", "re-record", "rerecord", "redo the recording", "record it again")),
    ActionSpec("compile", "Compile", "flow", True, "Turn the recording into a workflow (uses a compile credit)",
               ("compile", "compile it", "compile again", "try compiling again", "retry compile")),
    ActionSpec("compile_later", "Compile later", "none", False, "Leave the recording for now"),
    ActionSpec("add_credits", "Add credits", "none", False, "Open billing"),
    ActionSpec("show_details", "Show details", "none", False, "Show what went wrong"),
    ActionSpec("answer_clarify", "Answer questions", "flow", False, "Show the next open question",
               ("next question", "ask me", "ask me the questions", "show the questions")),
    ActionSpec("edit", "Open Human Edit", "nav", False, "Open the step-by-step editor",
               ("edit", "open human edit", "edit the steps", "human edit"), target="edit"),
    ActionSpec("test", "Test it", "flow", True, "Run the workflow in the browser to check it works",
               ("test", "test it", "run the test", "run a test", "try it", "test again", "run it")),
    ActionSpec("stop_test", "Stop the test", "flow", False, "Stop the test that is running",
               ("stop", "stop the test", "stop it", "cancel the test")),
    ActionSpec("autofix", "Fix it", "flow", True, "Let Studio try to fix the failed step (one checked change at a time)",
               ("fix it", "auto fix", "autofix", "try to fix it", "fix the test")),
    ActionSpec("resume_autofix", "Keep fixing", "flow", True, "Continue the automatic fix attempts",
               ("keep fixing", "continue fixing", "resume fixing", "fix it", "try again")),
    ActionSpec("approve", "Approve", "flow", True, "Approve the passing test for packaging (never publishes)",
               ("approve", "approve it")),
    ActionSpec("publish", "Publish", "nav", False, "Open the publish page",
               ("publish", "publish it", "open publish"), target="publish"),
    ActionSpec("open_workflow", "Open workflow", "nav", False, "Open one of the person's workflows", target="workflow"),
)}


def spec(action_id: str) -> ActionSpec | None:
    return ACTIONS.get(action_id)


def routable(allowed: list[str]) -> list[ActionSpec]:
    """The allowed actions chat can act on (those with a table entry that is not card-only)."""
    return [ACTIONS[a] for a in allowed if a in ACTIONS and ACTIONS[a].kind != "none"]
