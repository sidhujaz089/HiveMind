"""t616: ask_q16 must never send a conversation larger than num_ctx -- Ollama drops the oldest
message to fit, that is the only user message, and Qwen's template answers HTTP 500
"no user query found in messages". Shapes below are the three captured refusals'."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ask_q16 as A

ok = fail = 0
def check(name, cond):
    global ok, fail
    print(f"  [{'ok' if cond else 'FAIL'}]   {name}")
    ok += bool(cond); fail += not cond

def captured():
    # 2026-10-02T03:08:04Z: u35927 then 21 assistant/tool messages, 183,437 chars in all
    m = [{"role": "user", "content": "u" * 35927}]
    for n in (16241, 123, 1045, 661, 719, 30153, 30153, 30152, 12553, 9613, 1790, 13827):
        m += [{"role": "assistant", "content": "", "tool_calls": [{}]}, {"role": "tool", "content": "t" * n}]
    return m

m = captured()
before = sum(len(x["content"]) for x in m)
cut = A.fit_context(m, 65536, 24000)
after = sum(len(x["content"]) for x in m)
limit = (65536 - 21845) * A.CHARS_PER_TOKEN
check(f"the captured 500 shape ({before} chars) is cut to fit ({after} <= {limit:.0f})", after <= limit)
check("the user message is untouched", m[0]["content"] == "u" * 35927)
check("the latest tool result is untouched", m[-1]["content"] == "t" * 13827)
check("an elided result says what happened", "ELIDED" in m[2]["content"])
check("cut reports the characters removed", cut > 0 and before - after == cut)

small = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": ""}, {"role": "tool", "content": "x" * 5000}]
check("a conversation that fits is left alone", A.fit_context(small, 65536, 24000) == 0 and small[2]["content"] == "x" * 5000)
check("no num_ctx means no guard", A.fit_context(captured(), None, 24000) == 0)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
