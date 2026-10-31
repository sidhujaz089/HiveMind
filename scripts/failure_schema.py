#!/usr/bin/env python3
"""failure_schema.py -- the APPENDER: every failed build lands in workspace/failure-schema.json (t366).

Jas, 2026-09-20: "every failure creates a mechanical part of the schema so there is self
improvement and less repetitive failures." The schema existed (10 classes, written by hand);
nothing wrote to it. This is the writer, to the contract on t366 (2026-09-22):

  - KEY ON CLASS. A repeat appends to that class's instances[]; it never adds a second row.
  - It writes class, cause_category, instances[], reason, evidence -- NEVER `defense`. The
    defense is a judgement and belongs to the Manager (root-cause-analysis Rule 0).
  - Entries are never deleted. Same lock discipline as taskstore (a read-modify-write without
    one loses a row -- the failure class this file exists to prevent).
  - IDEMPOTENT: the same (class, chunk, at) appends nothing the second time.

classify() reads only what the harness itself printed -- coder.py's fixed attempt words and its
refusal lines -- so a class is a fact about the log, not a guess about the model.
"""
import argparse
import fcntl
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA = REPO / "workspace" / "failure-schema.json"
CHANGE_LEFT_IN_TREE = "change-left-in-tree"
REQUIRED_FIELDS = ("path", "head", "actor", "disposition", "lands_via")
LANDS_VIA = re.compile(r"^t\d+$")
CONTROL_NOT_ENFORCED = 'control-not-enforced'
CLASS_REQUIRED = {CONTROL_NOT_ENFORCED: ('application_endpoint', 'binding_check')}
ARTIFACT = re.compile(r'[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*(?::[0-9]+)?')


# (pattern in the attempt trail / log tail, class, cause_category). First match wins, most
# specific first. Classes that already exist in the hand-written schema keep their names.
RULES = [
    (r"undeclared failure:", "baseline-refusal-undeclared", "environment-precondition"),
    # A8, WIRED (2026-10-30): the marker coder writes when oracle_conflict.classify() fires. The CLASS is the cause
    # category here on purpose -- there is no blame that fits "the brief asked for two incompatible things", and
    # inventing one would route an impossible patch to the worker. destination_for_class looks the class up directly.
    (r"ORACLE-CONFLICT\b", "oracle-conflict", "oracle-conflict"),
    (r"ORACLE-OVERLAP\b", "oracle-overlap", "oracle-overlap"),
    (r"NEEDS-SCOPE|WINDOW FULL", "window-full", "content"),
    (r"RemoteDisconnected|connection dropped|ConnectionResetError", "remote-disconnected-external-restart", "external"),
    # THE THREE SHAPES THAT MADE THE 319 (2026-10-25). The catch-all class held 319 instances whose
    # evidence each has a signature, so the classifier was the thing that was unclassified:
    #   * a scratch directory that vanished mid-run -- the sweeper took /tmp/coder-... while the build
    #     was using it. That is a HARNESS fault: the work was fine and the ground moved.
    #   * a server that never answered -- Connection refused / urlopen / NoServer. EXTERNAL, not a
    #     harness bug: nothing was asked, so there is nothing to repair in the code.
    #   * a line-range edit refused because it cut a multi-line string -- the coder's own refusal, the
    #     same class the "refused (anchor" rule already covers, in a different wording.
    (r"No such file or directory.*(coder-|session-review|hivemind-tmp|/tmp/)",
     "harness-scratch-dir-vanished", "harness-capability"),
    (r"Connection refused|urlopen error|NoServer|Errno 111",
     "model-server-not-answering", "external"),
    (r"refused \(lines", "harness-edit-application", "harness-capability"),
    # WHAT THE CATCH-ALL ACTUALLY HELD (measured 2026-10-25: 319 instances). By far the largest shape,
    # 170 of them, is the marker the writer leaves when it TRUNCATES the evidence:
    #   [TRUNCATED -- the end of workspace/run-logs/...
    # No classifier can categorise that, and calling it "unclassified" reads as "the instrument is
    # broken" when the truth is "the RECORD was cut". So it gets its own class and the stale-record
    # blame: the work may have been perfectly fine. This is docs/skills/never-truncate-a-record's
    # lesson arriving as data -- and the fix is in the writer, not in the classifier.
    (r"\[TRUNCATED", "evidence-truncated", "stale-record"),
    (r"Timeout|timed out", "run-timed-out", "external"),
    (r"HTTPError: HTTP Error 4\d\d", "model-rejected-the-request", "harness-capability"),
    (r"code shape|BAD-SHAPE", "bad-shape-unbound-or-removed", "harness-capability"),
    (r"library check|LIBRARY-BLOCK|commit gate", "gate-after-acceptance", "harness-capability"),
    (r"python syntax|refused \(anchor|unusable|truncated at the limit|no-op|gave 0 new",
     "harness-edit-application", "harness-capability"),
    (r"tests fail", "tests-failed", "content"),
]


def classify(text):
    """(class, cause_category) for a failed build's attempt trail + log tail."""
    last = ""
    for line in (text or "").splitlines():
        if re.match(r"\s*attempt \d+ ", line):
            last = line
    for probe in (last, text or ""):              # the LAST attempt decides; else anything in the log
        for pat, cls, cause in RULES:
            if probe and re.search(pat, probe):
                return cls, cause
    return "unclassified", "unclassified"


def validate_credential_exposure(fields) -> list[str]:
    """Return a list of field-named problems for a credential-exposure record."""
    if fields is None:
        fields = {}
    problems: list[str] = []

    for field in CREDENTIAL_EXPOSURE_REQUIRED_FIELDS:
        if _is_blank(fields.get(field)):
            problems.append(f"{field} is required")

    disposition = fields.get("disposition")
    if not _is_blank(disposition):
        if not isinstance(disposition, str) or disposition.strip() not in CREDENTIAL_EXPOSURE_DISPOSITIONS:
            problems.append("disposition must be one of rotated, filed")
        elif disposition.strip() == "rotated":
            for field in CREDENTIAL_EXPOSURE_ROTATION_FIELDS:
                if _is_blank(fields.get(field)):
                    problems.append(f"{field} is required")
        elif disposition.strip() == "filed":
            for field in CREDENTIAL_EXPOSURE_FILED_FIELDS:
                if _is_blank(fields.get(field)):
                    problems.append(f"{field} is required")
            task_id = fields.get("task_id")
            if not _is_blank(task_id):
                if not CREDENTIAL_EXPOSURE_TASK_ID.match(str(task_id).strip()):
                    problems.append(f"task_id must be a task number like t1332, not {task_id!r}")

    return problems


def validate_landing(cls, fields):
    """Refuse incomplete landing fields; non-landing classes skip validation."""
    if cls == CREDENTIAL_EXPOSURE:
        problems = validate_credential_exposure(fields)
        if problems:
            raise ValueError("credential-exposure: " + "; ".join(problems))
        return None
    if cls != CHANGE_LEFT_IN_TREE:
        return None
    if not isinstance(fields, dict):
        raise ValueError("fields is not a dict")
    for field in REQUIRED_FIELDS:
        if field not in fields:
            raise ValueError(f"missing {field}")
        value = fields[field]
        if value is None or str(value).strip() == "":
            raise ValueError(f"empty {field}")
    lands_via = str(fields["lands_via"])
    if lands_via != lands_via.strip() or not LANDS_VIA.match(lands_via):
        raise ValueError("invalid lands_via")
    return None
def _resolvable_artifact(value):
    '''True when value names a file that exists, with a line that exists in it.'''
    text = str(value if value is not None else '').strip()
    if not text or not ARTIFACT.fullmatch(text):
        return False
    path, sep, line = text.partition(':')
    found = REPO / path
    if not found.is_file():
        return False
    if sep:
        total = sum(1 for _ in found.open(encoding='utf-8', errors='replace'))
        return 1 <= int(line) <= total
    return True


def validate(row):
    '''The problems with a row, as one string naming each offending field; '' when there are none.'''
    required = CLASS_REQUIRED.get(str((row or {}).get('class') or ''))
    if not required:
        return ''
    problems = []
    for field in required:
        value = (row or {}).get(field)
        if value is None or not str(value).strip():
            problems.append(field + ' is required')
        elif field == 'binding_check' and not _resolvable_artifact(value):
            problems.append('binding_check must name a file in the repo, with a line that '
                            'exists in it: ' + str(value))
    return '; '.join(problems)

def append(cls, cause, chunk, at, reason, evidence, path=None, fields=None, *, extra=None):
    """Add one sighting. Returns 'new-class', 'appended' or 'duplicate'."""
    path = Path(path or SCHEMA)
    validate_landing(cls, fields)
    payload = dict(fields or {})
    payload.update(extra or {})
    problems = validate({'class': cls, 'cause_category': cause, 'reason': reason,
                         'evidence': evidence, **payload})
    if problems:
        raise ValueError(problems)
    if cls == CONTROL_NOT_ENFORCED:
        reason_words = set(re.findall(r'[A-Za-z]{4,}', str(reason).lower()))
        endpoint_words = set(re.findall(r'[A-Za-z]{4,}', str(payload.get('application_endpoint') or '').lower()))
        if not reason_words & endpoint_words:
            raise ValueError("refused: reason must name the control named by application_endpoint; "
                             "binding_check is the artefact that binds it: " + str(reason))
    with open(path.with_suffix(".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = json.loads(path.read_text(encoding="utf-8"))
        rows = d.setdefault("failures", [])
        row = next((r for r in rows if r.get("class") == cls), None)
        inst = {"at": at, "chunk": chunk, "evidence": evidence, **payload}
        if row is None:
            rows.append({"class": cls, "cause_category": cause, "instances": [inst],
                         "reason": reason, "evidence": evidence,
                         "added_by": "failure_schema.py", **payload})
            out = "new-class"
        elif any(i.get("at") == at and i.get("chunk") == chunk for i in row.get("instances", [])):
            return "duplicate"
        else:
            row.setdefault("instances", []).append(inst)
            out = "appended"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
        return out



def record_failure(chunk, at, trail_and_tail, path=None):
    """The one call the failure path makes."""
    cls, cause = classify(trail_and_tail)
    last = [l.strip() for l in (trail_and_tail or "").splitlines() if l.strip()]
    # NEVER STORE THE TRUNCATION NOTICE AS THE EVIDENCE (2026-10-25, t4410). cap() appends its
    # `[TRUNCATED ...]` marker LAST, and this used to take the last line -- so a capped log stored the
    # notice instead of what failed. Measured: 170 recorded failures carry nothing auditable, which is
    # why they could not be classified afterwards. The notice is not deleted: it is simply never
    # preferred over a line about the failure, and it is still kept when it is all there is.
    evidence = next((l for l in reversed(last) if re.match(r"attempt \d+ ", l)), None)
    if evidence is None:
        evidence = next((l for l in reversed(last) if not l.startswith("[TRUNCATED")),
                        last[-1] if last else "")
    return cls, append(cls, cause, chunk, at, f"classified mechanically from the build log ({cls})",
                       evidence, path=path)


# Constants for the self-contaminated-measurement row: a measurement is unusable until
# the row says how the instrument was removed from the population it measured.
SELF_CONTAMINATED_MEASUREMENT = "self-contaminated-measurement"
SELF_CONTAMINATED_MEASUREMENT_CLASS = SELF_CONTAMINATED_MEASUREMENT
SELF_CONTAMINATED_MEASUREMENT_REQUIRED_FIELDS = (
    "population_kind",
    "measured_population",
    "self_exclusion",
)
SELF_CONTAMINATED_MEASUREMENT_COUNTING_POPULATION_KINDS = ("count", "scan", "listing")
SELF_CONTAMINATED_MEASUREMENT_EVAL_POPULATION_KINDS = ("eval",)
SELF_CONTAMINATED_MEASUREMENT_POPULATION_KINDS = SELF_CONTAMINATED_MEASUREMENT_COUNTING_POPULATION_KINDS + SELF_CONTAMINATED_MEASUREMENT_EVAL_POPULATION_KINDS
SELF_CONTAMINATED_MEASUREMENT_PATTERN_PROVENANCE_VALUES = ("literal", "runtime")
SELF_CONTAMINATED_MEASUREMENT_CASE_PROVENANCE_VALUES = ("authored-before", "authored-after-implementation")
SELF_CONTAMINATED_MEASUREMENT_SELF_EXCLUSION_MARKERS = ("grep -v", "--exclude-self")
SELF_CONTAMINATED_MEASUREMENT_SELF_EXCLUSION_DIGIT_RUN = 2
# Aliases without the long prefix, for callers that reach for the shorter names.
POPULATION_KINDS = SELF_CONTAMINATED_MEASUREMENT_POPULATION_KINDS
COUNTING_POPULATION_KINDS = SELF_CONTAMINATED_MEASUREMENT_COUNTING_POPULATION_KINDS
EVAL_POPULATION_KINDS = SELF_CONTAMINATED_MEASUREMENT_EVAL_POPULATION_KINDS
PATTERN_PROVENANCE_VALUES = SELF_CONTAMINATED_MEASUREMENT_PATTERN_PROVENANCE_VALUES
CASE_PROVENANCE_VALUES = SELF_CONTAMINATED_MEASUREMENT_CASE_PROVENANCE_VALUES
SELF_EXCLUSION_MARKERS = SELF_CONTAMINATED_MEASUREMENT_SELF_EXCLUSION_MARKERS
SELF_EXCLUSION_DIGIT_RUN = SELF_CONTAMINATED_MEASUREMENT_SELF_EXCLUSION_DIGIT_RUN
CREDENTIAL_EXPOSURE = "credential-exposure"
CREDENTIAL_EXPOSURE_REQUIRED_FIELDS = ("exposure_surface", "disposition")
CREDENTIAL_EXPOSURE_ROTATION_FIELDS = ("rotation_command", "output_recorded")
CREDENTIAL_EXPOSURE_FILED_FIELDS = ("task_id", "owner", "monitor_trigger")
CREDENTIAL_EXPOSURE_DISPOSITIONS = ("rotated", "filed")
CREDENTIAL_EXPOSURE_TASK_ID = re.compile(r"^t\d{2,4}$")


def _is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _is_nonempty_string(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_digit_run(value: str, minimum: int) -> bool:
    run = 0
    for ch in value:
        if ch.isdigit():
            run += 1
            if run >= minimum:
                return True
        else:
            run = 0
    return False


def _has_bracket_char_class(value: str) -> bool:
    for i, ch in enumerate(value):
        if ch == "[" and i + 2 < len(value) and value[i + 2] == "]" and value[i + 1].isalpha():
            return True
    return False


def _has_file_line_anchor(value: str) -> bool:
    for i, ch in enumerate(value):
        if ch == ":" and i + 1 < len(value) and value[i + 1].isdigit():
            return True
    return False


def _is_resolvable_self_exclusion(value) -> bool:
    if not isinstance(value, str):
        return False
    if any(marker in value for marker in SELF_CONTAMINATED_MEASUREMENT_SELF_EXCLUSION_MARKERS):
        return True
    if _has_bracket_char_class(value):
        return True
    if _has_digit_run(value, SELF_CONTAMINATED_MEASUREMENT_SELF_EXCLUSION_DIGIT_RUN):
        return True
    if _has_file_line_anchor(value):
        return True
    return False


def validate_row(row, schema=None) -> list[str]:
    """Return a list of field-named problems for a self-contaminated-measurement row."""
    errors: list[str] = []
    if row.get("class") != SELF_CONTAMINATED_MEASUREMENT:
        return errors

    for field in SELF_CONTAMINATED_MEASUREMENT_REQUIRED_FIELDS:
        if _is_blank(row.get(field)):
            errors.append(f"{field} is required")

    population_kind = row.get("population_kind")
    if not _is_blank(population_kind) and population_kind not in SELF_CONTAMINATED_MEASUREMENT_POPULATION_KINDS:
        errors.append("population_kind must be one of count, scan, listing, eval")

    measured_population = row.get("measured_population")
    if not _is_blank(measured_population):
        if not isinstance(measured_population, dict):
            errors.append("measured_population must be a dict carrying a non-empty 'source' and 'selector'")
        else:
            if not _is_nonempty_string(measured_population.get("source")):
                errors.append("measured_population must carry a non-empty 'source'")
            if not _is_nonempty_string(measured_population.get("selector")):
                errors.append("measured_population must carry a non-empty 'selector'")

    pattern_provenance = row.get("pattern_provenance")
    if population_kind in SELF_CONTAMINATED_MEASUREMENT_COUNTING_POPULATION_KINDS and _is_blank(pattern_provenance):
        errors.append("pattern_provenance is required")
    if not _is_blank(pattern_provenance) and pattern_provenance not in SELF_CONTAMINATED_MEASUREMENT_PATTERN_PROVENANCE_VALUES:
        errors.append("pattern_provenance must be exactly 'literal' or 'runtime'")

    case_provenance = row.get("case_provenance")
    if population_kind == "eval" and _is_blank(case_provenance):
        errors.append("case_provenance is required")
    if not _is_blank(case_provenance) and case_provenance not in SELF_CONTAMINATED_MEASUREMENT_CASE_PROVENANCE_VALUES:
        errors.append("case_provenance must be exactly 'authored-before' or 'authored-after-implementation'")

    self_exclusion = row.get("self_exclusion")
    if not _is_blank(self_exclusion) and not _is_resolvable_self_exclusion(self_exclusion):
        errors.append(
            "self_exclusion must name a resolvable artefact: grep -v, --exclude-self, "
            "a bracket character class, a pid list, or a file:line anchor"
        )

    if population_kind == "eval":
        authored_by = row.get("authored_by")
        approved_by = row.get("approved_by")
        if _is_blank(authored_by):
            errors.append("authored_by is required")
        if _is_blank(approved_by):
            errors.append("approved_by is required")
        if _is_nonempty_string(authored_by) and _is_nonempty_string(approved_by) and authored_by == approved_by:
            errors.append("authored_by and approved_by must not be equal")

    return errors


# A prose-defect scanner (t733): a sentence that cites a live defect and carries no disposition
# is a legal terminating act -- prose has no next actor, so nothing on disk makes it incomplete.
# The phrases below are the fallback; the live list is read from the schema row that records the
# class, so a future scope that adds a defect word changes the record that owns the class, not
# the code that acts on it.
DEFAULT_VOCABULARY = (
    'unmerged', 'undiagnosed', 'unreviewed', 'unowned', 'untriaged', 'uncommitted',
    'undisposed', 'unaddressed', 'unhealthy', 'uninstalled', 'not patched',
    'not recorded', 'no collector', 'no mechanism', 'no owner', 'no closure path',
    "didn't fire", 'did not apply', 'no-op', 'should probably', 'is unavailable',
    'there is no', 'left open', 'left alone',
)

PROSE_DEFECT_CLASS = 'defect-cited-without-disposition'

# A disposition is a task number, a declined that carries a reason, or a declared_open that
# carries its close condition. 'unresolved' is not one of them -- it is the word the finding file
# has been saying for days while nothing acted.
DISPOSITION_PATTERNS = (
    re.compile(r'\bt\d{2,4}\b'),                        # tNNN: two to four digits
    re.compile(r'\bdeclined\s*[:\-]\s*(\S.{2,})\s*$'),  # a marker carrying a reason
)
DECLARED_OPEN_MARK = re.compile(r'\bdeclared[_\-]?open\b')
CLOSE_CONDITION_MARK = re.compile(r'close[_\ -]condition')
DECLARED_OPEN_MIN_REASON = 8  # non-space characters required between the two markers


def _disposition_on(lowered):
    """The disposition a lower-cased line carries, or None when it carries none."""
    for pattern in DISPOSITION_PATTERNS:
        match = pattern.search(lowered)
        if match:
            return match.group(0).strip()
    opened = DECLARED_OPEN_MARK.search(lowered)
    close = CLOSE_CONDITION_MARK.search(lowered)
    if opened and close:
        between = lowered[opened.end():close.start()]
        if sum(1 for ch in between if not ch.isspace()) >= DECLARED_OPEN_MIN_REASON:
            # The span between the two markers, not a truncation of the record: the whole line is
            # reported by find_undispositioned(), uncut.
            return lowered[opened.start():close.end()].strip()
    return None


def defect_vocabulary(path=None):
    """The defect phrases, taken from the schema row that records the class.

    The row is absent today, so DEFAULT_VOCABULARY is the normal path and a missing row is not an
    error. A schema that cannot be read is NAMED on stderr: a silent fallback here is the very
    defect this scanner exists to catch, and a vocabulary invented in code is one the record
    cannot see.
    """
    path = Path(path) if path is not None else SCHEMA
    try:
        rows = json.loads(path.read_text(encoding='utf-8')).get('failures') or []
        for row in rows:
            if isinstance(row, dict) and row.get('class') == PROSE_DEFECT_CLASS:
                vocab = row.get('trigger_vocabulary')
                if isinstance(vocab, list) and vocab and all(isinstance(w, str) for w in vocab):
                    return list(vocab)
                return list(DEFAULT_VOCABULARY)
        return list(DEFAULT_VOCABULARY)
    except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
        print(f'failure_schema: {path}: schema unreadable, using DEFAULT_VOCABULARY ({exc})',
              file=sys.stderr)
        return list(DEFAULT_VOCABULARY)


def find_undispositioned(lines, vocabulary=None, schema_path=None):
    """Every line that cites a defect word and carries no disposition: 1-based, whole-line."""
    vocab = list(vocabulary) if vocabulary is not None else defect_vocabulary(schema_path)
    findings = []
    for number, raw in enumerate(lines, start=1):
        lowered = str(raw).lower()
        trigger = next((phrase for phrase in vocab if phrase in lowered), None)
        if trigger is None:
            continue
        disposition = _disposition_on(lowered)
        if disposition is not None:
            continue
        # 'quote' is the whole line, never a slice: a truncated record cannot be opened again.
        findings.append({'line': number, 'quote': str(raw).strip(), 'trigger': trigger,
                         'disposition': disposition})
    return findings


# JAS'S THREE (2026-10-25): worker-output-rejected / harness-fault / stale-record. The schema's
# cause_category is the mature four-way taxonomy; this maps it onto his words so the count answers
# "are the bugs holding the agents back?". `external` stays its own answer -- see the data file.
BLAME_DEFAULT = {
    "content": "worker-output-rejected",
    "harness-capability": "harness-fault",
    "environment-precondition": "harness-fault",
    "stale-record": "stale-record",
    "external": "external",
}


def blame_map(schema_path=None):
    """cause_category -> blame, from the schema's `_blame` block with the code's default behind it."""
    try:
        d = json.loads(Path(schema_path or SCHEMA).read_text(encoding="utf-8"))
        out = dict(BLAME_DEFAULT)
        for k, v in (d.get("_blame") or {}).items():
            if not str(k).startswith("_"):
                out[k] = str(v)
        return out
    except (OSError, ValueError, AttributeError):
        return dict(BLAME_DEFAULT)


def blame_of(cause_category, mapping=None):
    """The blame for one cause_category; 'unclassified' when the taxonomy has no answer, which is a
    finding in itself rather than a default that quietly counts as the worker's fault."""
    return (mapping or blame_map()).get(cause_category, "unclassified")


def _failure_rows(d):
    """(class, row) for every recorded failure, tolerating both shapes the file has carried: a dict
    keyed by class, or a list of rows. It is a list today -- reading it as a dict is how the first
    version of blame_report() died with AttributeError, which is the kind of thing an oracle catches."""
    rows = d.get("failures") or []
    if isinstance(rows, dict):
        return [(k, v) for k, v in rows.items() if isinstance(v, dict)]
    return [(str(r.get("class") or "?"), r) for r in rows if isinstance(r, dict)]


def blame_report(schema_path=None):
    """({blame: count}, {blame: [class, ...]}) over the recorded failures."""
    d = json.loads(Path(schema_path or SCHEMA).read_text(encoding="utf-8"))
    m = blame_map(schema_path)
    counts, classes = {}, {}
    for cls, row in _failure_rows(d):
        cat = (row or {}).get("cause_category")
        b = blame_of(cat, m)
        n = len((row or {}).get("instances") or []) or 1
        counts[b] = counts.get(b, 0) + n
        classes.setdefault(b, []).append(cls)
    return counts, classes


def upstream_items(schema_path=None):
    """The item each HARNESS-caused failure class owes, so a harness fault becomes work, not a note.

    Jas, 2026-10-25, on the direction he gave Claude: when something fails, the reason becomes something
    mechanical upstream. For harness-capability and environment-precondition the schema's own routing
    rule already says what to do -- repair the harness, then re-dispatch the chunk UNCHANGED -- so the
    item is generated here instead of being remembered by a session.

    The note carries a [failure-schema:<class>] marker: filing the same class twice is a no-op, which
    is what stops a recurring failure from becoming a new task every night.
    """
    d = json.loads(Path(schema_path or SCHEMA).read_text(encoding="utf-8"))
    m = blame_map(schema_path)
    out, seen = [], set()
    for cls, row in _failure_rows(d):
        if cls in seen:
            continue
        seen.add(cls)
        cat = row.get("cause_category")
        if blame_of(cat, m) != "harness-fault":
            continue
        n = len((row or {}).get("instances") or [])
        out.append({
            "class": cls, "cause_category": cat, "instances": n,
            "title": f"Harness fault, {n} instance(s): {cls} -- repair the harness, not the brief",
            "note": (f"[failure-schema:{cls}] cause_category {cat} -- blame harness-fault. The routing "
                     f"rule for this category is: repair the harness and re-dispatch the chunk "
                     f"UNCHANGED; never edit the brief for it. Evidence: workspace/failure-schema.json "
                     f"-> failures.{cls}. ORACLE: the next dispatch of a chunk that hit this class "
                     f"does not hit it again."),
        })
    return out


def main(argv):
    """The reporter: name every defect citation that carries no disposition. Exit 1 = findings."""
    parser = argparse.ArgumentParser(
        prog='failure_schema.py',
        description='report defect citations in prose that carry no disposition')
    parser.add_argument('--input', action='append', default=None, help='file to scan (repeatable)')
    parser.add_argument('--schema', default=None,
                        help='failure schema to read the vocabulary from '
                             '(default: workspace/failure-schema.json)')
    parser.add_argument('--blame', action='store_true',
                        help='count the recorded failures by who is at fault (Jas 2026-10-25)')
    parser.add_argument('--upstream', action='store_true',
                        help='print the item each harness-caused failure class owes')
    args = parser.parse_args(argv)
    if args.blame or args.upstream:
        try:
            if args.upstream:
                items = upstream_items(args.schema)
                for it in items:
                    print(f"  {it['title']}")
                    print(f"    note: {it['note']}")
                print(f"  {len(items)} harness-caused class(es) owe an item")
                return 0
            counts, classes = blame_report(args.schema)
            for b in sorted(counts, key=lambda k: -counts[k]):
                print(f"  {b:24s} {counts[b]:5d}  {', '.join(sorted(classes.get(b, []))[:4])}")
            return 0
        except (OSError, ValueError) as exc:
            print(f'failure_schema: cannot read the schema: {exc}', file=sys.stderr)
            return 2
    if not args.input:
        print(parser.format_usage().rstrip()
              + '\n  --input <file> is required: a scan with nothing to read is not a verdict',
              file=sys.stderr)
        return 2
    vocab = defect_vocabulary(args.schema)
    findings = 0
    for name in args.input:
        path = Path(name)
        try:
            text = path.read_text(encoding='utf-8')
        except Exception as exc:  # noqa: BLE001 -- an unreadable input is never a clean result
            print(f'failure_schema: cannot read {path}: {exc}', file=sys.stderr)
            return 2
        for row in find_undispositioned(text.splitlines(), vocabulary=vocab):
            print(f'{path}:{row["line"]}: [{row["trigger"]}] {row["quote"]}')
            findings += 1
    print(f'failure_schema: {findings} undispositioned defect citation(s) across '
          f'{len(args.input)} file(s)', file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print(classify(sys.stdin.read()))
    else:
        raise SystemExit(main(sys.argv[1:]))

