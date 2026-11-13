"""The gates of the proof picker (compiler/proof_gates.py): each builds a check only from evidence the steps hold."""

from conxa_compile.compiler.effects import build_effect
from conxa_compile.compiler.navigation_effects import generalize_url_pattern
from conxa_compile.compiler.proof_gates import GATES, later_step, row_with_identity, url_stays, value_equals
from conxa_compile.compiler.step_key import step_keys
from conxa_core.models.skill_spec import Assertion, EFFECT_PROBES, SkillStep, ValidationBlock

FORM = "https://app.test/form"


def _typed(binding="item_title", url=FORM, action="type"):
    return SkillStep(action={"action": action}, url=url, input_binding=binding, value="{{" + binding + "}}")


def _save(url=FORM, assertions=(), label="Save", kind_event=None):
    ev = kind_event or {"action": {"action": "click"}, "target": {"inner_text": label}}
    effect = build_effect(ev, is_commit=True)
    return SkillStep(action={"action": "click"}, url=url, validation=ValidationBlock(effect=effect, assertions=list(assertions)))


def _landed(pattern):
    return Assertion(type="url_pattern", target=pattern, required=False)


LANDED = "^" + __import__("re").escape("https://app.test") + "/items/[^/?#]+"


def test_every_gate_is_for_a_known_probe():
    assert set(GATES) <= set(EFFECT_PROBES)


# ── url_stays ──
def test_url_stays_is_built_when_the_step_landed_on_a_different_address():
    steps = [_save(assertions=[_landed(LANDED)])]
    check = url_stays(steps, 0)
    assert check.probe == {"type": "url_stays", "pattern": LANDED} and check.rung == "accepted"


def test_url_stays_must_not_be_built_without_a_different_landing():
    assert url_stays([_save()], 0) is None, "no landed address recorded"
    already = _save(url="https://app.test/items/12", assertions=[_landed(LANDED)])
    assert url_stays([already], 0) is None, "the step began on an address the pattern already matches"
    assert url_stays([_save(assertions=[_landed("(")])], 0) is None, "a broken pattern"
    same_template = _save(url="https://app.test/items/new?draft=1", assertions=[_landed(generalize_url_pattern("https://app.test/items/new"))])
    assert url_stays([same_template], 0) is None, "before and after generalise to the same template"


# ── row_with_identity ──
def test_row_with_identity_needs_a_value_typed_earlier_on_the_same_page_and_a_save_or_send():
    check = row_with_identity([_typed(), _save()], 1)
    assert check.probe == {"type": "row_with_identity", "key": "{{item_title}}", "delta": 1} and check.rung == "result_exists"
    assert row_with_identity([_save()], 0) is None, "nothing was typed"
    assert row_with_identity([_typed(url="https://app.test/other"), _save()], 1) is None, "typed on another page"
    assert row_with_identity([SkillStep(action={"action": "click"}, url=FORM), _save()], 1) is None, "a click is not a typed value"
    upload = SkillStep(action={"action": "upload_intent"}, url=FORM,
                       validation=ValidationBlock(effect=build_effect({"action": {"action": "upload_intent"}})))
    assert row_with_identity([_typed(), upload], 1) is None, "an upload has its own go-and-look"


# ── value_equals ──
def test_value_equals_is_a_save_only_and_names_the_typing_step_by_its_key():
    steps = [_typed(), _save()]
    check = value_equals(steps, 1)
    assert check.probe == {"type": "value_equals", "field_step": step_keys(steps)[0], "value": "{{item_title}}"}
    assert check.rung == "completed", "never result_exists: a restored draft must not meet the raised bar"
    send = _save(label="Send invoice", kind_event={"action": {"action": "click"}, "target": {"inner_text": "Send invoice"}})
    send.validation.effect.kind = "send"
    assert value_equals([_typed(), send], 1) is None
    assert value_equals([_save()], 0) is None


# ── later_step ──
def test_later_step_needs_a_later_step_that_consumes_what_this_one_makes():
    download = SkillStep(action={"action": "click"}, url=FORM, produces=["downloaded_file_1"],
                         validation=ValidationBlock(effect=build_effect({"action": {"action": "download_observed"}})))
    upload = SkillStep(action={"action": "upload"}, url=FORM, consumes=["downloaded_file_1"])
    other = SkillStep(action={"action": "click"}, url=FORM)
    steps = [download, other, upload]
    check = later_step(steps, 0)
    assert check.probe == {"type": "later_step", "step": step_keys(steps)[2]} and check.witness == "later_step"
    assert later_step([download, other], 0) is None, "nothing later uses it"
    assert later_step([download, SkillStep(action={"action": "upload"}, url=FORM, consumes=["downloaded_file_9"])], 0) is None
    assert later_step([other, upload], 0) is None, "a step that makes nothing"
