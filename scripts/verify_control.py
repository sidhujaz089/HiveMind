"""Ask Q16 and Q4, in identical words, whether each item is answerable.

    python3 scripts/verify_control.py               the 8 CANNOT-DETERMINE traps (default)
    python3 scripts/verify_control.py --set control the 4 answerable items

A "yes" must QUOTE A LINE. That makes the claim checkable instead of a matter of authority:
a quoted line that is not in the excerpt is a confabulation, and its "yes" is void. Neither
model is told what the other said, and neither is a candidate.
"""
import json, os, re, sys, time, urllib.request
sys.path.insert(0, "/home/user/ai-workspace/projects/local-inference")
import settings
import jsonx

BANK = json.load(open("/home/user/ai-workspace/workspace/exams/bank-senior-endurance.json"))

# TWO SETS, AND WHICH ONE RAN IS RECORDED IN THE OUTPUT (2026-09-03).
#
#   control  4 ANSWERABLE items (2 DEFECT, 2 NOT-A-DEFECT). A judge SHOULD say determinable
#            here, so a "no" is the judge being over-cautious. Cheap, and it proves the
#            harness end to end.
#   traps    all 8 CANNOT-DETERMINE items. The honest answer is determinable=false. A "yes"
#            here is the failure that matters, and it is the set the SENIOR decision rests
#            on -- `laguna-xs.2` and `gpt-oss:20b` are tied except on these.
#
# Until now this file could only run the control, and the set was chosen by editing line 12.
# An edit is not a record: the output file did not say which set produced it, so a control
# result and a trap result were indistinguishable on disk.
SETS = {
    "control": ([i for i in BANK["items"] if i["answer"] == "DEFECT"][:2] +
                [i for i in BANK["items"] if i["answer"] == "NOT-A-DEFECT"][:2]),
    "traps":   [i for i in BANK["items"] if i["answer"] == "CANNOT-DETERMINE"],
}
# WHICH SET, resolved inside main(). At module level this read the IMPORTER's sys.argv and
# could sys.exit() during an import -- so a tool that merely imported this file could be killed
# by its own command line. See scripts/tests/test_verify_control_guard.py.


def chosen_set():
    name = os.environ.get("VERIFY_SET", "traps")
    if "--set" in sys.argv:
        name = sys.argv[sys.argv.index("--set") + 1]
    if name not in SETS:
        sys.exit(f"unknown --set {name!r}; expected one of {sorted(SETS)}")
    return name
# NAMES ALIGNED 2026-09-03, found by the Duo. This said "qwen-sa" while every brief and
# duo_verify.py's SUBJECT said "qwen3.8:27b-mtp-q4_K_M". Both are digest 22130167c4c2 so the
# WEIGHTS were always identical and no result was wrong -- but the Duo spent five of its
# fourteen rounds establishing that, because a name in the code disagreeing with a name in
# the brief is indistinguishable from a real defect until you check. That is the
# claim-vs-code drift class (t213), costing rounds rather than correctness.
# `qwen-sa` also belongs to the OLD Q4-class duo pair; using it here overloaded the name.
JUDGES = {"Q16": "qwen3.8:27b-mtp-bf16", "Q4": "qwen3.8:27b-mtp-q4_K_M"}

# ONE QUESTION, ONE COPY (2026-09-03). This file used to carry its own hardcoded ASK while
# the bank shipped an `instructions` field that nothing here read. Editing the bank's copy
# changed nothing and the re-run came back identical to the decisecond -- the wrong file was
# fixed, and the second copy was free to disagree with what candidates are actually asked.
#
# Now the shared half comes FROM THE BANK, so the judges and the candidates are held to the
# same standard by construction. Only the response shape differs, because this script asks
# about one excerpt at a time and the exam asks about many.
# SUBSTITUTION IS str.replace, NOT str.format -- AND THAT IS NOT A STYLE CHOICE.
# The bank's instructions contain a literal JSON example, `{"i07": {"verdict": ...}}`, so
# .format() reads those braces as placeholders and raises KeyError '"i07"'. Building this
# prompt from the bank is what introduced the collision: the old standalone copy escaped its
# own braces and never saw the bank's. The failure was not loud -- every call raised, each
# was caught per item, and the run printed a full table reading "0/16 correct" as though the
# judges had answered badly. A harness fault wearing the costume of a result.
ASK = BANK["instructions"] + """

--------------------------------------------------------------------
Below is ONE excerpt.

{body}

Answer for this excerpt alone, as JSON:
  {"determinable": true|false,
   "verdict": "DEFECT" | "NOT-A-DEFECT" | null,
   "line": "the exact line from the excerpt you based it on, quoted verbatim, or null"}

determinable=false is the same answer as CANNOT-DETERMINE above, and carries the same rule:
if you cannot quote a line from THIS excerpt that settles it, the honest answer is false."""


def prompt_for(body):
    """The question for one excerpt. `replace`, so braces on either side survive."""
    if "{body}" not in ASK:
        raise AssertionError("the prompt lost its {body} slot -- every judge would be asked "
                             "about nothing, and would answer")
    return ASK.replace("{body}", body)


def ask(model, body):
    # think "low" (t330): an extraction into one JSON object, not code across many turns.
    b = settings.request("coder", model, [{"role": "user", "content": prompt_for(body)}],
                         num_predict=3000, think="low")
    r = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(b).encode(),
                               headers={"Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(r, timeout=1800))
    if d.get("done_reason") == "length":   # t238: a cut-off reply is said, not misread
        print(f"  NOTE: reply hit its num_predict limit (done_reason=length) -- it is cut off",
              file=sys.stderr)
    txt = (d.get("message", {}).get("content") or "")
    objs = jsonx.objects(txt)
    return objs[0] if objs else {"parse_error": txt}


def line_present(quoted, body):
    if not quoted:
        return None
    return quoted.strip() in "\n".join(re.sub(r"^\s*\d+\|\s?", "", l)
                                       for l in body.splitlines())


def main():
    """The experiment. NOT run on import -- see scripts/tests/test_verify_control_guard.py.

    Until 2026-09-09 this block sat at module level, so `import verify_control` looped
    over TRAPS calling BOTH judges and wrote the results file. Measured in one accidental
    run: 74.4s and 47.9s on a single item. That made the file untestable -- an oracle
    could stub urlopen and still never reach an assertion -- and made it an Ollama
    consumer that never takes the model lease, purely as a side effect of being imported.
    """
    out = []
    SET = chosen_set()
    TRAPS = SETS[SET]
    OUT = f"/home/user/ai-workspace/workspace/exams/trap-verification-{SET.upper()}.json"
    print(f"[set={SET}  {len(TRAPS)} item(s)  expected answer: "
          f"{'determinable' if SET == 'control' else 'NOT determinable'}]", flush=True)
    for it in TRAPS:
        row = {"id": it["id"], "position": it["position"], "file": it["file"]}
        for judge, tag in JUDGES.items():
            t = time.time()
            try:
                a = ask(tag, it["body"])
            except Exception as e:
                a = {"error": str(e)}
            det = a.get("determinable")
            quoted = a.get("line")
            present = line_present(quoted, it["body"])
            row[judge] = {"determinable": det, "verdict": a.get("verdict"),
                          "line": (quoted or ""), "line_present": present,
                          "seconds": round(time.time() - t, 1),
                          **({"parse_error": a["parse_error"]} if "parse_error" in a else {}),
                          **({"error": a["error"]} if "error" in a else {})}
        out.append(row)
        print(json.dumps(row), flush=True)

    json.dump({"set": SET, "expected_determinable": SET == "control",
               "judges": JUDGES, "rows": out}, open(OUT, "w"), indent=1)
    print(f"DONE -> {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
