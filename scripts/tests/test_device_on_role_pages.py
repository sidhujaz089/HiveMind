#!/usr/bin/env python3
'''t1114 oracle: the DEVICE a role runs on, on the Roles index and the role page.

Jas, 2026-10-13: the device on the role configuration page. A role device is DERIVED from its
model -- SERVERS.json servers[<server>].models has the model, .device names the device,
devices[<device>] gives that device memory budget -- so there is no second setting that can
disagree with the model.

No network: SERVERS.json is read from disk, and the page tests stub the live-server helpers
(role_store.live_models probes servers over HTTP) and the engine-ceiling helper, which goes
through infer, whose server objects answer health checks.
'''
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'projects' / 'app-repo'))
sys.path.insert(0, str(ROOT / 'scripts'))
import roles_pages as rp

FAILS = []
MISSING = [n for n in ('_servers_doc', 'device_for_model', 'device_box') if not hasattr(rp, n)]
MODELS = ROOT / 'projects' / 'local-inference' / 'models'


def check(what, cond):
    print(('  ok   ' if cond else '  FAIL ') + what)
    if not cond:
        FAILS.append(what)


def real_doc():
    return json.loads((MODELS / 'SERVERS.json').read_text(encoding='utf-8'))


def model_servers(doc):
    '''model -> the server ids whose models block lists it, computed from the real file.'''
    out = {}
    for sid, entry in (doc.get('servers') or {}).items():
        for m in (entry.get('models') or {}):
            out.setdefault(m, []).append(sid)
    return out


def default_device(doc):
    '''The device of the server SERVERS.json marks default, the main server, the GX10 today.'''
    servers = doc.get('servers') or {}
    hits = sorted(str((e or {}).get('device')) for e in servers.values()
                  if (e or {}).get('default') and (e or {}).get('device'))
    return hits[0] if hits else None


def expected_device(doc, model):
    '''Computed from SERVERS.json, never stored: the default server device when a model is on
    more than one server, else the device of the one server that lists it.'''
    servers = doc.get('servers') or {}
    hits = [(servers.get(sid) or {}) for sid in model_servers(doc).get(model, [])]
    dflt = sorted(str(e.get('device')) for e in hits if e.get('default') and e.get('device'))
    if dflt:
        return dflt[0]
    rest = sorted({str(e.get('device')) for e in hits if e.get('device')})
    return rest[0] if rest else default_device(doc)


def only_on(doc, device):
    '''Models the real file serves from exactly one server, and that server is on `device`.'''
    servers = doc.get('servers') or {}
    return sorted(m for m, sids in model_servers(doc).items()
                  if len(sids) == 1 and (servers.get(sids[0]) or {}).get('device') == device)


def test_missing_api():
    for n in MISSING:
        check('roles_pages.%s() exists: the change this oracle asks for' % n, False)


def test_the_device_follows_the_model():
    if MISSING:
        return
    doc = real_doc()
    gx = only_on(doc, 'gx10')
    tp = only_on(doc, 'thinkpad')
    check('SERVERS.json names a model only the gx10 serves: ' + (gx[0] if gx else 'NONE'), bool(gx))
    check('SERVERS.json names a model only the ThinkPad serves: ' + (tp[0] if tp else 'NONE'), bool(tp))
    if gx:
        check('a model only the gx10 serves resolves to gx10, from the device field in the file',
              rp.device_for_model(gx[0], doc)[0] == 'gx10')
    if tp:
        check('the model the ThinkPad serves resolves to thinkpad',
              rp.device_for_model(tp[0], doc)[0] == 'thinkpad')
        check('the device entry, its budget, comes back with the id',
              rp.device_for_model(tp[0], doc)[1] == (doc.get('devices') or {}).get('thinkpad'))
    shared = sorted(m for m, sids in model_servers(doc).items() if len(sids) > 1)
    check('SERVERS.json has a model served by two servers to test the shared case: '
          + (shared[0] if shared else 'NONE'), bool(shared))
    if shared:
        check('a model served by two servers resolves to the device of the default server',
              rp.device_for_model(shared[0], doc)[0] == expected_device(doc, shared[0]))
    check('SERVERS.json declares a default server', bool(default_device(doc)))
    check('a model no server lists falls back to the device of the default server',
          rp.device_for_model('no-such-model-anywhere', doc)[0] == default_device(doc))
    check('an empty SERVERS.json yields no device and no crash',
          rp.device_for_model('swift', {'devices': {}, 'servers': {}}) == (None, None))
    if gx:
        check('asking twice gives the same answer, so it cannot depend on dict order',
              rp.device_for_model(gx[0], doc) == rp.device_for_model(gx[0], doc))


def test_the_rule_holds_for_every_model_in_the_real_file():
    if MISSING:
        return
    doc = real_doc()
    models = model_servers(doc)
    bad = sorted(m for m in models if rp.device_for_model(m, doc)[0] != expected_device(doc, m))
    check('every model in SERVERS.json resolves to the device its server declares: '
          + (', '.join(bad) if bad else 'all ' + str(len(models)) + ' models'), not bad)


def test_the_box_carries_the_budget_and_what_is_on_the_device():
    if MISSING:
        return
    doc = real_doc()
    gx = only_on(doc, 'gx10')
    tp = only_on(doc, 'thinkpad')
    gx_box = rp.device_box(gx[0], doc) if gx else ''
    check('the box names the device', 'gx10' in gx_box)
    check('the box says what the machine is, from the file', 'ASUS GX10' in gx_box)
    check('the box says the unit is GB', 'GB' in gx_box)
    check('the box gives the gx10 budget of 121 GB', '121 GB' in gx_box)
    check('the box gives the gx10 reserve of 8 GB', '8 GB' in gx_box)
    check('the box lists the models the device serves, not only the model of this role',
          'qwen3-embedding:0.6b' in gx_box)
    check('the gx10 box does not quote the ThinkPad budget of 24 GB', '24 GB' not in gx_box)
    tp_box = rp.device_box(tp[0], doc) if tp else ''
    check('the ThinkPad box names the device', 'thinkpad' in tp_box)
    check('the ThinkPad box gives its 24 GB budget', '24 GB' in tp_box)
    check('the ThinkPad box gives its 1.5 GB reserve', '1.5 GB' in tp_box)
    check('the ThinkPad box lists the model it serves', bool(tp) and tp[0] in tp_box)
    check('the ThinkPad box does not quote the gx10 budget of 121 GB', '121 GB' not in tp_box)
    nb = rp.device_box('no-such-model-anywhere', doc)
    check('an unplaceable model gets a sentence naming the model, not an empty box',
          bool(nb.strip()) and 'no-such-model-anywhere' in nb)
    check('that sentence says which file it looked in', 'SERVERS.json' in nb)


def _store(gx_model, tp_model):
    '''a two-role store, one model on each device, with real lane counts'''
    return {'roles': {
        'manager': {'name': 'Manager', 'kind': 'model', 'model': gx_model, 'what': 'scopes work',
                    'tasks': [], 'tools': {}, 'attributes': {}, 'lanes': 4},
        'auditor': {'name': 'Auditor', 'kind': 'model', 'model': tp_model, 'what': 'reviews',
                    'tasks': [], 'tools': {}, 'attributes': {}, 'lanes': 1}}}


def test_the_pages_show_the_device():
    if MISSING:
        return
    doc = real_doc()
    gx, tp = only_on(doc, 'gx10'), only_on(doc, 'thinkpad')
    if not (gx and tp):
        check('SERVERS.json has one model only on the gx10 and one only on the ThinkPad', False)
        return
    saved = (rp.rs.load, rp.rs.live_models, rp.rs.declared_models, rp._engine_ceiling, rp._servers_doc)
    rp.rs.load = lambda *a, **k: _store(gx[0], tp[0])
    rp.rs.live_models = lambda *a, **k: ({}, [])
    rp.rs.declared_models = lambda *a, **k: set()
    rp._engine_ceiling = lambda model: None
    rp._servers_doc = lambda: doc
    try:
        gx_box, tp_box = rp.device_box(gx[0], doc), rp.device_box(tp[0], doc)
        page = rp.role_page('manager')
        check('the role page embeds the helper box for the model of the role, not a paraphrase',
              gx_box in page)
        check('the Manager page names the gx10', 'gx10' in page)
        check('the Manager page does not quote the ThinkPad budget', '24 GB' not in page)
        apage = rp.role_page('auditor')
        check('the Auditor page embeds the helper box for its model', tp_box in apage)
        check('the Auditor page names the ThinkPad', 'thinkpad' in apage)
        check('the Auditor page does not quote the gx10 budget', '121 GB' not in apage)
        idx = rp.index_page()
        check('the Roles index shows the device for every role', gx_box in idx and tp_box in idx)
    finally:
        rp.rs.load, rp.rs.live_models, rp.rs.declared_models = saved[0], saved[1], saved[2]
        rp._engine_ceiling, rp._servers_doc = saved[3], saved[4]


if __name__ == '__main__':
    for _name in sorted(globals()):
        if _name.startswith('test_'):
            globals()[_name]()
    print('\n  %d failure(s)' % len(FAILS))
    sys.exit(1 if FAILS else 0)
