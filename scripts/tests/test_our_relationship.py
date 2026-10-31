"""Oracle for t292 -- the Our Relationship app's named mechanic, frozen before the module exists.

Jas's request (workspace/app-feedback/2026-09-14T030548Z--new-idea--i-m-using-an-app-called-our-relationship.md): activities a couple do "on the side", then "it's revealed what each person said", then they talk. Four properties protect that one sentence:

  1. blind by construction -- the form served to A never carries B's answer, and is byte-identical whether or not B has answered;
  2. whole answers -- an answer is stored and returned whole, never cut;
  3. reveal completes only when both exist -- "pending" is a named state, never an empty string standing in for "said nothing";
  4. framework is data -- prompts.json is keyed by framework name, so adding "conflict-mediation" is a data edit, not a code change.

THE API THIS PINNED CONTRACT REQUIRES, in projects/our-relationship/app.py. `answers` is a plain nested dict the CALLER owns and passes in, mutated by record(): answers[framework][prompt_id][person] = the whole answer string. No server, no port, no files outside a temp dir is needed to satisfy this suite.

    load_prompts(path) -> dict mapping framework name -> list of prompt dicts,
        each prompt dict carrying "id", "text" and "talk" (the talk-it-over text).
    record(answers, framework, prompt_id, person, answer) -> None
    form_text(answers, framework, prompt_id, person, prompts) -> str
    reveal(answers, framework, prompt_id) -> dict with a "status" key whose value
        is "ready" only when both people have answered, "pending" otherwise.
        With status "ready", a key "answers" maps each person to their own whole answer.

Run: python3 scripts/tests/test_our_relationship.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "projects" / "our-relationship" / "app.py"

FRAMEWORK_A = "our-relationship"
FRAMEWORK_B = "conflict-mediation"
PERSON_A = "alex"
PERSON_B = "sam"
PROMPT_A = "p-a1"
PROMPT_B = "p-b1"

# Two DISTINCT texts, one per person. Distinct values are the point: a build that returns the
# same string twice, or attributes both to one person, can pass an oracle built from one
# repeated placeholder. Built with chr(10).join (not string multiplication) so the failure
# report is readable.
ALEX_ANSWER = chr(10).join([
    "I feel most connected when we take a walk together.",
    "I would like us to plan one Saturday a month with no screens.",
    "The thing I did not say at dinner: I felt left out when the phone was out.",
])
SAM_ANSWER = chr(10).join([
    "I feel most connected when we cook together.",
    "I would like us to plan one weekday evening a week for a long talk.",
    "The thing I did not say: I keep the phone close because I worry about work.",
])

PROMPTS = {
    FRAMEWORK_A: [
        {"id": PROMPT_A, "text": "When do you feel most connected to me?",
         "talk": "Say what you heard back, in your own words, before responding."},
        {"id": "p-a2", "text": "What is one thing you would like more of this month?",
         "talk": "Name one concrete change each of you will try."},
    ],
    FRAMEWORK_B: [
        {"id": PROMPT_B, "text": "What is the argument you keep having, stated as a need?",
         "talk": "State the need, not the grievance. Then the other repeats it back."},
        {"id": "p-b2", "text": "What did you need in that moment that you did not ask for?",
         "talk": "Ask before assuming. One question each."},
    ],
}


def load_app():
    """Import projects/our-relationship/app.py by path -- today this is what fails."""
    if not MODULE_PATH.is_file():
        raise AssertionError(
            "module missing: " + str(MODULE_PATH) + " -- create it with load_prompts, record,"
            " form_text and reveal as documented in this file's docstring")
    import importlib.util
    spec = importlib.util.spec_from_file_location("our_relationship_app", MODULE_PATH)
    if spec is None:
        raise AssertionError("no import spec for " + str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("load_prompts", "record", "form_text", "reveal"):
        if not callable(getattr(module, name, None)):
            raise AssertionError("module is missing " + name + "()")
    return module


def write_prompts(tmp):
    """A prompts.json this suite owns, in the shape load_prompts documents: a dict keyed by
    framework. The framework keys are data, so the framework argument is not a code path."""
    path = Path(tmp) / "prompts.json"
    path.write_text(json.dumps(PROMPTS), encoding="utf-8")
    return str(path)


def test_prompts_are_keyed_by_framework():
    app = load_app()
    tmp = tempfile.mkdtemp()
    prompts = app.load_prompts(write_prompts(tmp))
    if not isinstance(prompts, dict):
        raise AssertionError("load_prompts must return a dict keyed by framework, got "
                            + type(prompts).__name__)
    for framework in (FRAMEWORK_A, FRAMEWORK_B):
        if framework not in prompts:
            raise AssertionError("no entry for framework " + repr(framework)
                                + "; load_prompts keys the bank by framework")
    by_id = {p["id"]: p for p in prompts[FRAMEWORK_A]}
    if PROMPT_A not in by_id:
        raise AssertionError("prompt " + PROMPT_A + " missing from framework " + FRAMEWORK_A)
    if by_id[PROMPT_A]["text"] != PROMPTS[FRAMEWORK_A][0]["text"]:
        raise AssertionError("prompt text must be returned whole and exact; got "
                            + repr(by_id[PROMPT_A]["text"]))
    if by_id[PROMPT_A]["talk"] != PROMPTS[FRAMEWORK_A][0]["talk"]:
        raise AssertionError("the talk-it-over text must come back whole; got "
                            + repr(by_id[PROMPT_A]["talk"]))
    # NEGATIVE: the framework argument selects real data. A flat bank that ignores it passes
    # every "present" assertion and fails this one.
    for prompt in PROMPTS[FRAMEWORK_B]:
        if prompt["id"] in by_id:
            raise AssertionError("framework " + FRAMEWORK_A + " must not expose prompt id "
                                + prompt["id"] + ", which belongs to " + FRAMEWORK_B)


def test_prompt_lookup_is_scoped_to_its_framework():
    app = load_app()
    tmp = tempfile.mkdtemp()
    prompts = app.load_prompts(write_prompts(tmp))
    answers = {}
    app.record(answers, FRAMEWORK_A, PROMPT_A, PERSON_A, ALEX_ANSWER)
    # A prompt id from framework B, asked in framework A, is not the same question. Reading
    # the bank flat serves ALEX_ANSWER here and leaks across frameworks.
    leaked = app.form_text(answers, FRAMEWORK_B, PROMPT_A, PERSON_B, prompts)
    if ALEX_ANSWER in leaked:
        raise AssertionError("form_text served an answer stored under framework " + FRAMEWORK_A
                            + " for prompt id " + PROMPT_A + " asked in framework "
                            + FRAMEWORK_B + "; the bank lookup must be keyed by framework too")


def test_the_form_a_person_sees_is_blind():
    app = load_app()
    tmp = tempfile.mkdtemp()
    prompts = app.load_prompts(write_prompts(tmp))
    answers = {}
    app.record(answers, FRAMEWORK_A, PROMPT_A, PERSON_B, SAM_ANSWER)
    form = app.form_text(answers, FRAMEWORK_A, PROMPT_A, PERSON_A, prompts)
    # NEGATIVE: none of the partner's text reaches the page. A form is a page, so line
    # presence is checked as substrings of the whole body.
    for line in SAM_ANSWER.split(chr(10)):
        if line in form:
            raise AssertionError("form_text exposed the partner's answer on the page: "
                                + repr(line[:80]))
    # NEGATIVE: no marker naming the partner. A per-person label naming both people is a
    # leak in a framework with more than two participants, and it is the shape a "who has
    # answered" list takes.
    if PERSON_B in form:
        raise AssertionError("form_text named the other participant: " + PERSON_B)
    # POSITIVE: the prompt text is shown, whole, to the person filling it in.
    if PROMPTS[FRAMEWORK_A][0]["text"] not in form:
        raise AssertionError("form_text must show the prompt text whole")


def test_the_form_is_the_same_bytes_whether_the_partner_answered():
    """The strongest form of blind by construction. A page that shows "Sam has answered --
    reveal when you are both done" answers the question this app exists to keep unasked."""
    app = load_app()
    tmp = tempfile.mkdtemp()
    prompts = app.load_prompts(write_prompts(tmp))
    empty = {}
    answered = {}
    app.record(answered, FRAMEWORK_A, PROMPT_A, PERSON_B, SAM_ANSWER)
    blank = app.form_text(empty, FRAMEWORK_A, PROMPT_A, PERSON_A, prompts)
    filled = app.form_text(answered, FRAMEWORK_A, PROMPT_A, PERSON_A, prompts)
    if blank != filled:
        raise AssertionError("form_text differs once the partner has answered: the page exposes"
                            " that the partner answered, which is the whole point of answering"
                            " on the side")


def test_an_answer_is_stored_and_returned_whole():
    app = load_app()
    tmp = tempfile.mkdtemp()
    prompts = app.load_prompts(write_prompts(tmp))
    answers = {}
    app.record(answers, FRAMEWORK_A, PROMPT_A, PERSON_A, ALEX_ANSWER)
    app.record(answers, FRAMEWORK_A, PROMPT_A, PERSON_B, SAM_ANSWER)
    ready = app.reveal(answers, FRAMEWORK_A, PROMPT_A)
    if ready.get("answers", {}).get(PERSON_A) != ALEX_ANSWER:
        raise AssertionError("answer for " + PERSON_A + " was not returned whole; a record cut"
                            " short is a record that lies")
    # NEGATIVE: no cap on the answer. Stored and returned whole, at any length.
    long_answer = "I need " + "space " * 900 + "to finish this thought"
    long_key = "p-a2"
    app.record(answers, FRAMEWORK_A, long_key, PERSON_A, long_answer)
    app.record(answers, FRAMEWORK_A, long_key, PERSON_B, SAM_ANSWER)
    long_ready = app.reveal(answers, FRAMEWORK_A, long_key)
    stored = long_ready.get("answers", {}).get(PERSON_A)
    if stored != long_answer:
        raise AssertionError("a long answer was cut: stored "
                            + str(0 if stored is None else len(stored)) + " chars of "
                            + str(len(long_answer)) + "; remove the cap")


def test_reveal_is_pending_until_both_have_answered():
    app = load_app()
    tmp = tempfile.mkdtemp()
    prompts = app.load_prompts(write_prompts(tmp))
    answers = {}
    app.record(answers, FRAMEWORK_A, PROMPT_A, PERSON_A, ALEX_ANSWER)
    ready = app.reveal(answers, FRAMEWORK_A, PROMPT_A)
    if ready.get("status") != "pending":
        raise AssertionError("status must be \"pending\" with one answer in; got "
                            + repr(ready.get("status")))
    # NEGATIVE: a blank field must not stand in for "this person said nothing".
    if ALEX_ANSWER in json.dumps(ready):
        raise AssertionError("reveal leaked the first answer before the second was in")


def test_reveal_completes_with_both_answers_attributed():
    app = load_app()
    tmp = tempfile.mkdtemp()
    prompts = app.load_prompts(write_prompts(tmp))
    answers = {}
    app.record(answers, FRAMEWORK_A, PROMPT_A, PERSON_A, ALEX_ANSWER)
    app.record(answers, FRAMEWORK_A, PROMPT_A, PERSON_B, SAM_ANSWER)
    ready = app.reveal(answers, FRAMEWORK_A, PROMPT_A)
    if ready.get("status") != "ready":
        raise AssertionError("status must be \"ready\" with both answers in; got "
                            + repr(ready.get("status")))
    got = ready.get("answers", {})
    if got.get(PERSON_A) != ALEX_ANSWER:
        raise AssertionError("reveal must attribute " + PERSON_A + "'s whole answer to "
                            + PERSON_A)
    if got.get(PERSON_B) != SAM_ANSWER:
        raise AssertionError("reveal must attribute " + PERSON_B + "'s whole answer to "
                            + PERSON_B)
    if ALEX_ANSWER == SAM_ANSWER:
        raise AssertionError("the two fixture answers must differ, or the test proves nothing")


def test_the_second_framework_is_reachable_through_the_same_code():
    """The request's second half: 'I want to make this bigger with other relationship
    frameworks.' A design that hard-codes the first framework is green on every other test
    and red here."""
    app = load_app()
    tmp = tempfile.mkdtemp()
    prompts = app.load_prompts(write_prompts(tmp))
    answers = {}
    app.record(answers, FRAMEWORK_B, PROMPT_B, PERSON_A, ALEX_ANSWER)
    app.record(answers, FRAMEWORK_B, PROMPT_B, PERSON_B, SAM_ANSWER)
    ready = app.reveal(answers, FRAMEWORK_B, PROMPT_B)
    if ready.get("status") != "ready":
        raise AssertionError("framework " + FRAMEWORK_B + " must work with no code change;"
                            " status was " + repr(ready.get("status")))
    if ready.get("answers", {}).get(PERSON_A) != ALEX_ANSWER:
        raise AssertionError("reveal must work for framework " + FRAMEWORK_B
                            + " with no code change")


if __name__ == "__main__":
    _tests = sorted((name, obj) for name, obj in list(globals().items())
                     if name.startswith("test_") and callable(obj))
    _failures = []
    for _name, _fn in _tests:
        try:
            _fn()
            print("ok " + _name)
        except Exception as _exc:
            _failures.append(_name)
            print("FAIL " + _name + ": " + str(_exc)[:500])
    print(str(len(_tests) - len(_failures)) + "/" + str(len(_tests)) + " passed")
    sys.exit(1 if _failures else 0)
