#!/usr/bin/env python3
"""A failure's CLASS decides where it goes, not the builder's rank (A7, 2026-10-30).

The brief: "A7 a failure's CLASS decides its destination, instead of escalate_up(builder_role(...))
(supervise.py:485); and fix escalate_up(None) -> embedder, a nonsense destination, verified."

Two things were wrong, and the second is measured, not argued:
  * supervise.escalate_failure() sent EVERY failure one rank up from whoever built it, so a broken harness and a
    model's bad answer travelled the same road -- rework became a funnel (the RCA's finding);
  * escalate_up(None) returned **'embedder'**, because "start from the bottom and take the highest seat that is
    not an agent" lands on the embedding model. A role that cannot build, scope or review was the destination
    for a failure whose builder could not even be named.

RED ON MAIN:
  BAD  an unknown builder does not escalate to the embedder          (got 'embedder')
  BAD  a harness fault goes to a technical seat, not one rank up     (got None)
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import role_store as R          # noqa: E402
import supervise as S           # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


d = R.load()

# --- the verified nonsense destination -----------------------------------------------------------
got = R.escalate_up(None, d)
check("an unknown builder does not escalate to the embedder", got != "embedder", got)
check("  it goes to the role that holds rework, because an unnamed builder needs re-scoping",
      got == R.tier_roles("rework", d)[0], f"got {got}, rework holders {R.tier_roles('rework', d)}")
check("  and that is a role that can actually do something", got and d["roles"][got].get("kind") in
      ("person", "model"), str(d["roles"].get(got)))

# --- the class decides ---------------------------------------------------------------------------
for blame, want in (("harness-fault", "technical"), ("worker-output-rejected", "rework"),
                    ("stale-record", "rework"), ("external", "technical")):
    got = S.destination_for_class(blame, d)
    check(f"{blame} goes to {want}", got in R.tier_roles(want, d), f"got {got!r}, {want} held by "
          f"{R.tier_roles(want, d)}")
check("an unclassified failure names no destination, so the caller keeps one-rank-up",
      S.destination_for_class("unclassified", d) is None, str(S.destination_for_class("unclassified", d)))
check("no class ever resolves to the embedder",
      all(S.destination_for_class(b, d) != "embedder"
          for b in ("harness-fault", "worker-output-rejected", "stale-record", "external", "unclassified")),
      "the embedder can neither build nor review")

# --- and the classifier feeds it ----------------------------------------------------------------
cls, cause = S.class_of("pytest: the reply was [TRUNCATED at 8192 tokens]")
check("the class comes from the failure schema's own vocabulary", (cls, cause) ==
      ("evidence-truncated", "stale-record"), f"{cls}/{cause}")
check("  and a truncated record routes to rework", S.destination_for_class(S.blame_of(cause), d) in
      R.tier_roles("rework", d), str(S.destination_for_class(S.blame_of(cause), d)))

print("test_failure_destination:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
