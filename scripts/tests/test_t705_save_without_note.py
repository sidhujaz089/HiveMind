#!/usr/bin/env python3
'''t705 -- Change assignments without notes (fail-first oracle).

Jas (workspace/tasks/t705--change-assignments-without-notes.md:12): 'I want to be
able to change assignments and save a task without needing a note. When I assign to
myself I enter a . and change the assignments. I want to make it easier and just be
able to press the add note button. It should be labeled as Save and serve this
function as well as saving a note if there is one.'

Where that lives -- projects/app-repo/feedback.py, def plan_detail(tid):

  3381  <form method="POST" action="/plan/note" enctype="multipart/form-data">
  3382    <input type="hidden" name="id" value="...">
  3383    <textarea name="text" required placeholder="Add a note for Claude Code..."></textarea>
  3386    <label class="f">{hold_select(meta.get('needs'))}   <- Assign to: is IN this form
  3389      <button class="wide" type="submit">Add note</button>

The assignment control sits inside a form whose note box is required, so the browser
refuses the submit while the box is empty -- that is why Jas types a filler dot just to
move a task. The server already accepts an empty note: the /plan/note branch
(feedback.py:3985-4001) guards the write with 'if text:' (3987-3988) and applies the
assignee unconditionally via ts.set_meta(tid, needs=hold_value(one('hold'))) (4000).
So the defect is in the rendered form only: the required attribute, and a button naming
only one of the form's three jobs.

RED ON MAIN for exactly those reasons, all in that one form. No live state: the store
lookup is stubbed at module level and the dossier card is stubbed to a marker, both
restored in a finally, so no task file, no lock file, no dossier file and nothing under
workspace/ is read as live data or written.
'''
import importlib
import sys
import traceback
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _P in (str(ROOT / 'projects' / 'app-repo'), str(ROOT / 'scripts')):
    if _P not in sys.path:
        sys.path.insert(0, _P)

import taskstore as ts                              # noqa: E402
feedback = importlib.import_module('feedback')      # noqa: E402  (no server: guard at 4141)

TID = 't705'

# The real front matter of workspace/tasks/t705--change-assignments-without-notes.md:1-10,
# plus needs: jas -- the state Jas described: the task is on him and he is moving it.
META = {
    'id': 't705',
    'title': 'Change assignments without notes',
    'area': 'new-idea',
    'kind': 'unfiled',
    'status': 'queued',
    'assigned': 'production',
    'source': ('workspace/app-feedback/2026-10-06T045822Z--new-idea--'
               'i-want-to-be-able-to-change-assignments.md'),
    'created': '2026-10-06T045822Z',
    'needs': 'jas',
}
# Real body text, in the '**YYYY-MM-DD · name**' note format ts.last_speaker matches.
BODY = ('I want to be able to change assignments and save a task without needing a note. '
        'When I assign to myself I enter a . and change the assignments.  I want to make it '
        'easier and just be able to press the add note button. It should be labeled as Save '
        'and serve this function as well as saving a note if there is one.\n'
        '\n'
        '## Notes\n'
        '\n'
        '**2026-10-09 · claude-code** — (Claude - Code) Back to production (Claude, 2026-10-09, '
        'Jas said get them built): nothing here waits on Claude.\n'
        '\n'
        '## Attachments\n')


class _Forms(HTMLParser):
    '''Collect every form with its textareas, inputs, selects (options + selected flags) and
    button labels, so the assertions read elements, not substrings of the page.'''

    def __init__(self):
        super().__init__()
        self.forms = []
        self._form = None
        self._select = None
        self._option = None
        self._button = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'form':
            self._form = {'action': a.get('action', ''),
                          'method': (a.get('method') or '').upper(),
                          'textareas': [], 'inputs': [], 'selects': [], 'buttons': []}
            self.forms.append(self._form)
        elif self._form is not None and tag == 'textarea':
            self._form['textareas'].append({'name': a.get('name'),
                                            'required': 'required in a',
                                            'placeholder': a.get('placeholder', '')})
        elif self._form is not None and tag == 'input':
            self._form['inputs'].append(a)
        elif self._form is not None and tag == 'select':
            self._select = {'name': a.get('name'), 'options': []}
            self._form['selects'].append(self._select)
        elif self._form is not None and tag == 'option' and self._select is not None:
            self._option = {'value': a.get('value', ''), 'selected': 'selected' in a}
        elif self._form is not None and tag == 'button':
            self._button = []

    def handle_data(self, data):
        if self._button is not None:
            self._button.append(data)

    def handle_endtag(self, tag):
        if tag == 'option' and self._option is not None:
            if self._select is not None:
                self._select['options'].append(self._option)
            self._option = None
        elif tag == 'select':
            self._select = None
        elif tag == 'button' and self._button is not None:
            self._form['buttons'].append(' '.join(''.join(self._button).split()))
            self._button = None
        elif tag == 'form':
            self._form = None


def _rendered():
    '''plan_detail() rendered for the real t705 front matter, with the store lookup and the
    dossier card stubbed so the oracle touches no disk. Both are patched on the module
    attribute, which is how plan_detail calls them, and restored in the finally.'''
    real_find = ts.find
    real_card = feedback.dossier_card

    def fake_find(tid):
        assert tid == TID, 'plan_detail was rendered for the wrong task id: ' + repr(tid)
        return dict(META), BODY

    def fake_card(tid):
        assert tid == TID, 'plan_detail asked the dossier for the wrong task: ' + repr(tid)
        return '<div class="card">DOSSIER-STUB</div>'

    ts.find = fake_find
    feedback.dossier_card = fake_card
    try:
        page = feedback.plan_detail(TID)
    finally:
        ts.find = real_find
        feedback.dossier_card = real_card
    assert page, 'plan_detail(' + TID + ') returned nothing -- the task page cannot be checked'
    assert 'DOSSIER-STUB' in page, (
        'plan_detail did not render the stubbed dossier section, so this is not the task page '
        'the assertions below are reading')
    return page


def _note_form():
    parser = _Forms()
    parser.feed(_rendered())
    parser.close()
    # The page carries several forms (Mark doing/Done/Cancel/move, and one Delete per
    # attachment), so the contract is the action, never the position.
    found = [f for f in parser.forms if f['action'] == '/plan/note']
    assert len(found) == 1, ('expected exactly one form posting to /plan/note, found '
                             + str(len(found)) + ' in '
                             + str([(f['action'], f['method']) for f in parser.forms]))
    return found[0]


def test_empty_note_box_can_be_submitted():
    '''The request itself: no required attribute on the note box, so Save works with an
    empty box and the assignment on the same form can be saved without filler text.'''
    form = _note_form()
    boxes = [t for t in form['textareas'] if t['name'] == 'text']
    assert len(boxes) == 1, ('the /plan/note form must render one textarea named text; found '
                             + str(len(boxes)) + ' in ' + str(form['textareas']))
    assert not boxes[0]['required'], (
        'textarea name=text carries the required attribute: with an empty note box the browser '
        'refuses the submit, so the Assign-to change on the same form cannot be saved without '
        'typing filler text (t705)')
    assert boxes[0]['placeholder'], (
        'the note box must keep its placeholder -- Save has to keep saving a note when there '
        'is one; found ' + repr(boxes[0]['placeholder']))


def test_the_submit_button_is_labelled_save():
    '''The submit button reads Save, and the note-only label is gone from this form.'''
    form = _note_form()
    labels = [b for b in form['buttons'] if b]
    assert labels, ('the /plan/note form renders no submit button with a label: '
                    + str(form['buttons']))
    assert 'Save' in labels, ('the submit button of the /plan/note form must read Save; found '
                              + str(labels))
    assert all('note' not in b.lower() for b in labels), (
        'a button in the /plan/note form still names only the note; t705 asks that it be '
        'labelled Save. Found ' + str(labels))


def test_save_still_carries_the_endpoint_the_id_and_the_assignment():
    '''Relabelling must not weaken the save. A build that drops the endpoint, the POST, the
    task id, the Assign-to select or its preselection passes the two tests above and no
    longer does what Jas asked; these assertions fail that build.'''
    form = _note_form()
    assert form['method'] == 'POST', ('the /plan/note form must post; method is '
                                      + repr(form['method']))
    ids = [i for i in form['inputs'] if i.get('name') == 'id']
    assert len(ids) == 1 and ids[0].get('value') == TID, (
        'the form must post the task id ' + TID + ' as input name=id; found ' + str(ids))
    selects = [s for s in form['selects'] if s['name'] == 'hold']
    assert len(selects) == 1, ('the Save form must contain the Assign-to control, the select '
                               'named hold (built by hold_select, read back by the handler as '
                               'one(hold)); found selects '
                               + str([s['name'] for s in form['selects']]))
    chosen = [o['value'] for o in selects[0]['options'] if o['selected']]
    assert len(chosen) == 1 and chosen[0], (
        'the Assign-to control must preselect the task owner, so Save keeps it with the right '
        'owner; found selected ' + str(chosen) + ' among '
        + str([(o['value'], o['selected']) for o in selects[0]['options']]))


def _main():
    names = sorted(n for n, v in globals().items() if n.startswith('test_') and callable(v))
    assert names, 'no test_ functions defined in this oracle'
    failed = []
    for name in names:
        try:
            globals()[name]()
        except Exception:
            failed.append(name)
            print('FAIL ' + name)
            print(traceback.format_exc())
        else:
            print('ok   ' + name)
    if failed:
        print('FAILED: ' + ', '.join(failed))
        return 1
    print('all ' + str(len(names)) + ' passed')
    return 0


if __name__ == '__main__':
    sys.exit(_main())
