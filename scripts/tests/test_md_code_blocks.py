#!/usr/bin/env python3
"""t316 (part 1): fenced code blocks in task notes render as code, each with a Copy button.

Jas: "I want the notes in tasks to support code blocks like the ones you use in chats that lets me
tap a copy button." RED on main: feedback.md_to_html has no fence handling -- a ``` line renders as
a paragraph and the lines inside are parsed as bullets and bold. GREEN when a fenced block becomes
one <pre> holding its lines verbatim (HTML-escaped, nothing inside parsed as markdown) with a Copy
button that copies that block's text.

md_to_html is pure: it is called directly on fixture text. No live state.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
sys.path.insert(0, str(ROOT / "scripts"))
import feedback  # noqa: E402

FAILS = []


def ck(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        FAILS.append(label)


def pres(h):
    return re.findall(r"<pre[^>]*>(.*?)</pre>", h, re.S)


def main():
    md = ("Run this:\n```bash\nls -la <dir>\n- not a bullet\n**not bold**\n```\n"
          "after `x`\n```\necho two\n```")
    h = feedback.md_to_html(md)
    blocks = pres(h)
    ck("each fenced block is one <pre>", len(blocks), 2)
    first = blocks[0] if blocks else ""
    ck("its lines are kept verbatim and HTML-escaped",
       all(s in first for s in ("ls -la &lt;dir&gt;", "- not a bullet", "**not bold**")), True)
    ck("nothing inside a fence is parsed as markdown",
       ("&bull; not a bullet" in h, "<b>not bold</b>" in h), (False, False))
    ck("the fence markers themselves are not shown", "```" in h, False)
    ck("one Copy button per block", len(re.findall(r"<button[^>]*>\s*Copy\s*</button>", h)), 2)
    ck("the button copies with the clipboard API", h.count("navigator.clipboard.writeText"), 2)
    ck("text around the blocks still renders", ("<p>Run this:</p>" in h, "<code>x</code>" in h), (True, True))
    ck("a note with no fence renders exactly as before",
       feedback.md_to_html("- a\n**b** `c`"),
       "<div class='bullet'>&bull; a</div>\n<p><b>b</b> <code>c</code></p>")
    u = feedback.md_to_html("```\nnever closed")
    ck("an unclosed fence still shows its text as code", "never closed" in "".join(pres(u)), True)
    print("test_md_code_blocks:", "FAILED" if FAILS else "ok")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
