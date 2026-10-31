#!/usr/bin/env python3
"""citation_audit.py -- does every file a Manager answer cites exist, and is every line it cites real?

t413 (Jas, 2026-09-25: "do we need a continuous hallucination measurement?"). One hand audit of
fifteen answers (2026-09-24) found no invented files and wrong line numbers; 300+ answers since
were never checked. This is the mechanical half -- no model -- run over every answer:

  path            a repo-relative path in backticks: must exist (now, or in git history)
  path:N / N-M    the file must have at least that many lines today

A path that never existed in git history is INVENTED. A path that existed and is gone is MOVED,
reported separately -- it was true when written. Line numbers are checked against today's file,
so an old answer's lines can drift; the report gives the rate per answer date so drift and
invention are not confused.

    python3 scripts/citation_audit.py            # summary
    python3 scripts/citation_audit.py --list     # every miss, by answer
"""
import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCES = [REPO / "workspace" / "run-logs" / "manager-queue" / "out",
           REPO / "workspace" / "manager-answers"]
CITE = re.compile(r"`((?:scripts|projects|workspace|docs)/[A-Za-z0-9_./-]+?\.[a-z]{1,6})(?::(\d+)(?:-(\d+))?)?`")
_HIST = {}


def ever_existed(rel):
    if rel not in _HIST:
        r = subprocess.run(["git", "-C", str(REPO), "log", "--all", "--format=%h", "-1", "--", rel],
                           capture_output=True, text=True, timeout=60)
        _HIST[rel] = bool(r.stdout.strip())
    return _HIST[rel]


PROPOSE = re.compile(r"\b(new|create[sd]?|creating|add(?:s|ed|ing)?|write|writes|proposed?|would|will|oracle|target|chunk)\b", re.I)


def proposed_paths(text):
    """Paths the answer PROPOSES rather than cites: a chunk's file/target, or a path on a line that
    says new/create/add/will. A file that does not exist yet is the point of those, not a lie."""
    out = set(re.findall(r'"(?:file|target|path)"\s*:\s*"([^"]+)"', text))
    for line in text.splitlines():
        if PROPOSE.search(line):
            out.update(m.group(1) for m in CITE.finditer(line))
    return out


def audit_text(text):
    """[(kind, citation)] for every citation: ok, invented, moved, proposed, line-past-end."""
    out = []
    proposed = proposed_paths(text)
    for m in CITE.finditer(text):
        rel, a, b = m.group(1), m.group(2), m.group(3)
        if "..." in rel or "*" in rel:
            continue                              # an elided or glob path is not a claim
        p = REPO / rel
        cite = m.group(0).strip("`")
        if not p.is_file():
            out.append(("moved" if ever_existed(rel) else
                        "proposed" if rel in proposed else "invented", cite))
            continue
        if a:
            n = sum(1 for _ in p.open(encoding="utf-8", errors="replace"))
            if int(b or a) > n:
                out.append(("line-past-end", f"{cite} (file has {n})"))
                continue
        out.append(("ok", cite))
    return out


# QUOTED CODE (t413's second half, 2026-10-02). A backtick span that looks like code -- a call, an
# assignment, attribute access, 15+ chars -- is a claim that the repository says exactly this.
SPAN = re.compile(r"`([^`\n]{15,200})`")
FILE_CITE = re.compile(r"^[\w./-]+\.[a-z]{1,6}(?::[\d,–\-/ ]+)?$")
REF = re.compile(r"(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*)\([\w ,=\"'.]*\)")
CODEISH = re.compile(r"[A-Za-z_]\w*\(|\w\s*=\s*\S|[A-Za-z_]\.[A-Za-z_]|\[\S")
BARE_PATH = re.compile(r"(?<![\w./-])((?:scripts|projects|workspace|docs)/[A-Za-z0-9_./-]+?\.[a-z]{1,6})(?::\d+(?:-\d+)?)?")


def _norm(t):
    return re.sub(r"\s+", " ", t).strip()


def code_corpus():
    """Every tracked .py/.sh/.json/.md under scripts/ and projects/, whitespace-normalised, as one
    string: one read, then a substring test per quote (a git grep per span took minutes)."""
    r = subprocess.run(["git", "-C", str(REPO), "ls-files", "scripts", "projects"],
                       capture_output=True, text=True, timeout=120)
    parts = []
    for rel in r.stdout.splitlines():
        if rel.endswith((".py", ".sh", ".json", ".md", ".service", ".timer")):
            try:
                parts.append(_norm((REPO / rel).read_text(encoding="utf-8", errors="replace")))
            except OSError as e:
                print(f"  NOTE: {rel} unreadable ({e}); quotes from it may read as unmatched", file=sys.stderr)
    return "\n".join(parts)


def audit_quotes(text, corpus):
    """[(kind, quote)]: quote-found or quote-unmatched, for every code-like backtick span that is
    not a path citation or a command line. Unmatched is not 'invented': the code may have changed
    since the answer, so it is a rate to watch, not a verdict on one answer."""
    out = []
    text = re.sub(r"```.*?```", " ", text, flags=re.S)        # fenced blocks are not inline quotes
    for m in SPAN.finditer(text):
        q = m.group(1)
        # NOT A QUOTE: a span that starts or ends with a space is the text BETWEEN two code spans
        # (the regex paired the wrong backticks); a file:line citation; an elision; a command line.
        if q != q.strip() or CITE.fullmatch(m.group(0)) or FILE_CITE.match(q) or "..." in q or "…" in q \
                or "<" in q or "→" in q or "->" in q or q.startswith(("python3 ", "git ", "bash ", "$ ", "NEW ", "[", "systemctl ")):
            continue
        if not CODEISH.search(q):
            continue
        # A REFERENCE TO A FUNCTION, not a quote of its line: `runledger.reconcile()` names a thing
        # that exists when `def reconcile(` does.
        ref = REF.fullmatch(q)
        if ref:
            found = f"def {ref.group(1)}(" in corpus
        else:
            found = _norm(q) in corpus
        out.append(("quote-found" if found else "quote-unmatched", q))
    return out
def audit_commit_citations(text, claim_ts, repo=None) -> list:
    """[(kind, cite, detail)] for every commit-hash citation: unknown-hash, predates-claim,
    touches-no-named-file. [] when every citation checks out."""
    if repo is None:
        repo = REPO
    findings = []
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    sentences = re.split(r"(?<=[.!?\n])\s+", text)
    for m in re.finditer(r"(?<![0-9a-fA-F])([0-9a-f]{7,40})(?![0-9a-fA-F])", text):
        token = m.group(1)
        if not any(c in "abcdef" for c in token):
            continue
        try:
            r = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", token + "^{commit}"],
                               capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as e:
            findings.append(("git-failed", token,
                             f"git cat-file -e {token}^{{commit}} timed out: {type(e).__name__}: {e}"))
            continue
        except OSError as e:
            findings.append(("git-failed", token,
                             f"git cat-file -e {token}^{{commit}} OSError: {type(e).__name__}: {e}"))
            continue
        if r.returncode != 0:
            findings.append(("unknown-hash", token,
                             f"{token} is not a commit in this repo; git stderr: {r.stderr.strip()}"))
            continue
        try:
            r = subprocess.run(["git", "-C", str(repo), "show", "-s", "--format=%aI", token],
                               capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as e:
            findings.append(("git-failed", token,
                             f"git show -s --format=%aI {token} timed out: {type(e).__name__}: {e}"))
            continue
        except OSError as e:
            findings.append(("git-failed", token,
                             f"git show -s --format=%aI {token} OSError: {type(e).__name__}: {e}"))
            continue
        if r.returncode != 0:
            findings.append(("git-failed", token,
                             f"git show -s --format=%aI {token} exit {r.returncode}: {r.stderr.strip()}"))
            continue
        author_raw = r.stdout.strip()
        try:
            author_dt = datetime.fromisoformat(author_raw)
        except (ValueError, TypeError) as e:
            findings.append(("bad-date", token,
                             f"cannot parse author date {author_raw!r}: {type(e).__name__}: {e}"))
            continue
        if author_dt > claim_ts:
            findings.append(("predates-claim", token,
                             f"{token} author date {author_dt.strftime('%H:%M:%S')} is after "
                             f"claim time {claim_ts.strftime('%H:%M:%S')}"))
            continue
        claim_sentence = next((s for s in sentences if token in s), "")
        named_paths = set(m2.group(1) for m2 in BARE_PATH.finditer(claim_sentence))
        if not named_paths:
            continue
        try:
            r = subprocess.run(["git", "-C", str(repo), "show", "--name-only", "--format=", token],
                               capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as e:
            findings.append(("git-failed", token,
                             f"git show --name-only {token} timed out: {type(e).__name__}: {e}"))
            continue
        except OSError as e:
            findings.append(("git-failed", token,
                             f"git show --name-only {token} OSError: {type(e).__name__}: {e}"))
            continue
        if r.returncode != 0:
            findings.append(("git-failed", token,
                             f"git show --name-only {token} exit {r.returncode}: {r.stderr.strip()}"))
            continue
        commit_files = set(f for f in r.stdout.splitlines() if f.strip())
        if not (commit_files & named_paths):
            findings.append(("touches-no-named-file", token,
                             f"{token} touches: {', '.join(sorted(commit_files))}; "
                             f"none of the paths named in the claim sentence"))
    return findings



def answers():
    for d in SOURCES:
        if d.is_dir():
            yield from sorted(f for f in d.rglob("*.md") if not f.name.endswith(".thinking.md"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--quotes", action="store_true", help="also check quoted code spans (reads the code once)")
    a = ap.parse_args(argv)
    totals, files = {}, 0
    corpus = code_corpus() if a.quotes else None
    for f in answers():
        body = f.read_text(encoding="utf-8", errors="replace")
        rows = audit_text(body) + (audit_quotes(body, corpus) if corpus is not None else [])
        if not rows:
            continue
        files += 1
        for kind, cite in rows:
            totals[kind] = totals.get(kind, 0) + 1
            if a.list and kind not in ("ok", "quote-found"):
                print(f"  {kind:14} {f.relative_to(REPO)}  {cite}")
    n = sum(totals.values())
    if not n:
        print("  citation audit: no citations found -- nothing was checked, which is not a pass")
        return 0
    bad = totals.get("invented", 0)
    qn = totals.pop("quote-found", 0) + totals.get("quote-unmatched", 0)
    qu = totals.pop("quote-unmatched", 0)
    n = sum(totals.values())
    if a.quotes:
        print(f"  quoted code: {qn} span(s) -- {qu} not found verbatim in the code today"
              + (f" ({100 * qu / qn:.1f}%)" if qn else "")
              + "\n  (a DIAGNOSTIC, not a hallucination rate: measured 2026-10-02, most unmatched spans are"
                "\n   paraphrase, proposed code or since-changed code, not invented text -- read --list)")
    print(f"  citation audit: {n} citation(s) in {files} answer(s) -- "
          + ", ".join(f"{k} {v}" for k, v in sorted(totals.items(), key=lambda x: -x[1]))
          + f"; invented rate {100 * bad / n:.1f}%")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
