#!/usr/bin/env python3
"""A paper that was not answered must not enter the record as a score of zero.

2026-09-03: gpt-oss:20b at think="high" returned an EMPTY reply -- it spent its whole
num_predict on reasoning and emitted no content, which settings.py's own note on `think` warns
about. exam.py handed "" to the scorer, which reported "real items: 0/18, traps: 0/8" and
wrote a result file.

That is the worst place in this repository for a harness fault to look like data. A zero on a
hiring paper is not a weak signal, it is the STRONGEST possible evidence against a candidate,
and it was produced by a run in which the candidate was never asked anything it could answer.

So exam.py raises ExamNotSat and writes NOTHING. This proves both halves: the refusal happens,
and the honest path still scores.
"""
import json
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
import exam                                                  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def with_reply(content, done_reason="stop", evals=10):
    def _open(req, timeout=None):
        return FakeResponse({"message": {"content": content}, "done_reason": done_reason,
                             "eval_count": evals})
    return _open


def main():
    fails = []
    paper = exam._normalise(json.loads(exam.PAPERS["ENDURANCE"].read_text()))
    real_open = urllib.request.urlopen

    cases = [
        ("an EMPTY reply", "", "stop", 4000),
        ("a whitespace-only reply", "   \n  ", "stop", 4000),
        ("a reply CUT OFF mid-answer", '{"i01": {"verdict": "DEFECT"', "length", 16000),
    ]
    for label, content, why, evals in cases:
        urllib.request.urlopen = with_reply(content, why, evals)
        try:
            exam.ask("fake-model:test", paper, 4096)
            print(f"  [BAD]   {label} was accepted")
            fails.append(f"exam.ask accepted {label}; the scorer would turn it into a 0/18 "
                         f"that reads as evidence against a candidate")
        except exam.ExamNotSat as e:
            print(f"  [ok]    {label} raises ExamNotSat")
            if str(e).find("not a zero") < 0 and why == "stop":
                pass                      # wording is not the contract; refusing is
        except Exception as e:            # noqa: BLE001
            print(f"  [BAD]   {label} raised {type(e).__name__}, not ExamNotSat")
            fails.append(f"{label} raised {type(e).__name__}: {e}")
        finally:
            urllib.request.urlopen = real_open

    # THE POSITIVE CONTROL. A refusal that refuses everything is not a check, it is a broken
    # script -- and this exam would then be unable to hire anyone for a reason nobody sees.
    good = json.dumps({it["id"]: {"verdict": it["answer"]} for it in paper["items"]})
    urllib.request.urlopen = with_reply(good, "stop", 5000)
    try:
        raw, why, evals = exam.ask("fake-model:test", paper, 4096)
        got = exam.parse(raw, [i["id"] for i in paper["items"]])
        r, t, _rows = exam.score(paper, got)
        # EXACTLY one answer per item. 52 for a 26-item paper was the real state before
        # 2026-09-03: the JSON branch stored uppercased ids that score() could never look up,
        # and the regex fallback silently re-found the same answers under the right ones.
        ok = len(got) == len(paper["items"]) and r > 0 and t > 0
        print(f"  [{'ok' if ok else 'BAD'}]    a complete reply still scores "
              f"(real={r}, traps={t}, {len(got)} answers parsed)")
        if not ok:
            fails.append(f"a perfect paper did not score: real={r} traps={t} parsed={len(got)}")
    except Exception as e:                # noqa: BLE001
        print(f"  [BAD]   a complete reply raised {type(e).__name__}: {e}")
        fails.append(f"the honest path is broken: {e}")
    finally:
        urllib.request.urlopen = real_open

    # Q4'S CATCH, 2026-09-03: the failure one level removed. A reply that is not empty, not
    # truncated, and simply does not answer -- which reached the scorer and produced 0/18.
    paper_ids = [i["id"] for i in paper["items"]]
    for label, content in [
            ("a polite refusal in prose",
             "I'm sorry, I was unable to complete this examination."),
            ("an answer to a DIFFERENT paper",
             '{"q1": {"verdict": "DEFECT"}, "q2": {"verdict": "SOUND"}}')]:
        got = exam.parse(content, paper_ids)
        refused = len(got) * 2 < len(paper_ids)
        print(f"  [{'ok' if refused else 'BAD'}]    {label} is below the half-answered "
              f"threshold ({len(got)}/{len(paper_ids)} found)")
        if not refused:
            fails.append(f"{label} parsed {len(got)} items and would have been scored")

    # AND THE THRESHOLD MUST NOT REJECT A CAUTIOUS CANDIDATE. CANNOT-DETERMINE is a real
    # answer; a model that abstains on everything must still be scored, and scored badly.
    allcd = json.dumps({i: {"verdict": "CANNOT-DETERMINE"} for i in paper_ids})
    got = exam.parse(allcd, paper_ids)
    ok = len(got) * 2 >= len(paper_ids)
    print(f"  [{'ok' if ok else 'BAD'}]    a candidate abstaining on everything is still "
          f"SCORED, not refused ({len(got)}/{len(paper_ids)})")
    if not ok:
        fails.append("the threshold rejects an all-abstain paper, which is a real answer")

    for x in fails:
        print("FAIL:", x)
    print("test_exam_not_sat:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
