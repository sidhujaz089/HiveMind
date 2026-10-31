"""One header, shared by every app on the tailnet.

Jas, 2026-09-05: "can all the apps in that row have the same header so it's easy to switch
between apps?" Before this, there were three different headers -- the Committee's tab row,
the Console's own bar, and chatui's -- and two apps (ttyd, Open WebUI) with none at all,
because they are third-party and cannot be given one. They are reached through wrapper
routes on the Committee that put this header above an iframe.

**This module is the single source of truth.** The reason it exists is drift: a tab list
copied into three files is a tab list that disagrees with itself the first time one app
gains a page. Import it, do not re-type it.

Reached from the sibling projects with the same `WORKSPACE/scripts` sys.path insert that
feedback.py's _reviewq() already uses.

**Colours are literal here, not inherited.** Each app has its own palette (the Committee is
warm and light, the Console is dark), so a header that referenced --bg or --line would
render differently in each one -- which is exactly the thing being fixed. The bar carries
its own colours so it looks identical everywhere.

**Every link is absolute.** The same list is rendered by four processes on four ports, so a
relative "/plan" would resolve against whichever app drew it.
"""

HOST     = "100.64.0.10"
HUB      = f"http://{HOST}:8091"          # the Committee (feedback.py)
MODELS   = f"http://{HOST}:8092/"         # chatui.py
CONSOLE  = f"http://{HOST}:8093/"         # console.py
TERMINAL = f"http://{HOST}:8094/"         # ttyd, agent-shell.service -- framed by /terminal
WEBUI    = f"http://{HOST}:8080/"         # Open WebUI in docker -- framed by /webui

# (key, href, label). The key is what an app passes as `active`; matching on the href broke
# as soon as the hrefs became absolute.
# ORDER, Jas 2026-09-27: Apps, Backlog, Production, Review, Lanes, Scripts, Misc, Terminal,
# Corpus, Models, Console, Web UI.
SECTIONS = [
    ("apps",     f"{HUB}/",         "Apps"),
    # Backlog REMOVED 2026-10-01 (Jas): each Production column carries its own queue as a collapsed
    # first stage. /plan redirects there; /plan/<id> is still the task page.
    # ADDED 2026-09-14. Jas asked for "a new tab on the hub that shows me the data you
    # have ... the tasks in production, the tasks in their queues as well. In case they are
    # stalled." Placed third, before Plan: the point of it is to see a stall BEFORE
    # planning around one, and on 2026-09-14 three watchers said "running" with their last
    # beat ~48 hours old.
    # RENAMED 2026-09-26 (Jas): Status -> Production, Plan -> Backlog. Keys and hrefs kept, so
    # every existing link and bookmark still lands.
    ("status",   f"{HUB}/status",   "Production"),
    ("review",   f"{HUB}/review",   "Review"),
    # ADDED 2026-09-26 (Jas: "a tab that just runs this script ... watch -n 15 lane_status.py").
    ("lanes",    f"{HUB}/lanes",    "Lanes"),
    # THE BOARD AS A CONVERSATION (Jas, 2026-10-29: "redesign the message board so it looks like a chat window ...
    # its own tab ... different colours" per speaker). The Lanes tab still summarises the board; this reads the same
    # rows as a conversation, so there is one board and not a second one that can disagree.
    ("chat",     f"{HUB}/chat",     "Chat"),
    # ADDED 2026-10-25 (Jas: "I want a Devices tab where I can adjust these types of settings ... In
    # the same area I want to see logs of what was going on when guards were triggered so I can judge
    # how to optimize"). The guard's levels are data it re-reads, so saving here needs no restart.
    ("devices",  f"{HUB}/devices",  "Devices"),
    # ADDED 2026-10-30 (scheduler rebuild, B1 and B3): the scheduler's settings as the code holds them, and every log.
    ("scheduler", f"{HUB}/scheduler", "Scheduler"),
    ("logs",     f"{HUB}/logs",     "Logs"),
    # ADDED 2026-10-26 (Jas): the network -- devices, how each is reached, and the services
    # around them. "a network map in the HiveMind Hub that has a tab just for this".
    ("network",  f"{HUB}/network",  "Network"),
    # ADDED 2026-09-26 (Jas: "I would like to be able to see everything. Can scripts be visible
    # in its own tab?"): script reviews on top, the script library below.
    ("scripts",  f"{HUB}/scripts",  "Scripts"),
    # ADDED 2026-09-26 (Jas: "I want to have eyes on everything"): brief reviews, supervision,
    # console answers, the night queue, model loads and model time -- the work no tab showed.
    ("misc",     f"{HUB}/misc",     "Misc"),
    ("terminal", f"{HUB}/terminal", "Terminal"),
    # ADDED 2026-09-15 (Jas: "a tab in the Hub called Corpus"): what is on the box -- models and scripts.
    # ADDED 2026-09-29 (Jas: "Let's call this tab Flow"): the workflow chart, tasks and links.
    ("flow",     f"{HUB}/flow",     "Flow"),
    ("corpus",   f"{HUB}/corpus",   "Corpus"),
    ("models",   f"{HUB}/models",   "Models"),   # the Hub page since 2026-09-29 (Roles; Chat moves next)
    # REMOVED 2026-10-01 (Jas): Console -- "broken, I haven't used it in a while" -- and Web UI,
    # whose Open WebUI was uninstalled the same day. console.py itself still runs (gate 5).
]

# Scoped under .appnav throughout: console.py and chatui.py both style a bare `header`
# selector, and an unscoped rule here would fight them.
CSS = """
/* Sticky, because Jas asked: "can it always show so I don't need to scroll up to the top
   to switch between areas?" On the Plan tab especially the page is far longer than a phone
   screen, and a nav you have to scroll back to is a nav you stop using.
   The title row is hidden once the bar is stuck, so what follows you down a long page is
   the tab strip alone (~42px) rather than the full bar (~95px) -- on a folding phone with
   the URL bar and keyboard already present, that difference is most of the reading area.
   position:sticky, not fixed: sticky stays inside the flex column that console.py and the
   iframe wrappers lay out, where fixed would break out of it and cover the content. */
.appnav { background:linear-gradient(135deg,#c8552b,#e08a2e 55%,#f0b429);
          color:#fff; padding:.9rem .9rem .55rem; margin:0 0 .9rem;
          box-shadow:0 2px 10px rgba(0,0,0,.14);
          position:sticky; top:0; z-index:60; }
.appnav .navwrap { max-width:none; margin:0; padding:0 .9rem; }
.appnav h1 { font-size:1.3rem; margin:0; letter-spacing:-.01em; color:#fff;
             font-weight:700; }
.appnav h1 .em { font-size:1.1rem; }
.appnav .tag { font-size:.8rem; opacity:.93; margin-top:.15rem; }
.appnav .tag a { color:#fff; text-decoration:underline; }
/* One row that scrolls sideways. Jas asked for this over wrapping: "can it scroll side to
   side so it doesn't flow down to 2 rows?" -- seven tabs cannot fit a phone at full
   labels, and a three-row header on a folding phone costs more than a swipe does. */
.appnav .tabs { display:flex; flex-wrap:nowrap; gap:.4rem; margin:.7rem 0 0;
                overflow-x:auto; overflow-y:hidden; -webkit-overflow-scrolling:touch;
                scrollbar-width:none; scroll-snap-type:x proximity; }
.appnav .tabs::-webkit-scrollbar { display:none; }
.appnav .tabs a { flex:0 0 auto; scroll-snap-align:start; white-space:nowrap;
                  padding:.5rem .85rem; border-radius:10px 10px 0 0;
                  text-decoration:none; font-weight:600; font-size:.92rem;
                  background:rgba(255,255,255,.22); color:#fff; }
.appnav .tabs a.on { background:#fffdf9; color:#c8552b; }
/* Stuck: drop the title and tighten the padding, keeping the tab strip. Transitions on
   max-height rather than display so it collapses smoothly instead of snapping. */
.appnav { transition:padding .18s ease; }
.appnav h1, .appnav .tag { transition:max-height .18s ease, opacity .18s ease,
                           margin .18s ease; max-height:4rem; overflow:hidden; }
.appnav.stuck { padding-top:.2rem; padding-bottom:.2rem; }
.appnav.stuck h1, .appnav.stuck .tag { max-height:0; opacity:0; margin:0; }
.appnav.stuck .tabs { margin-top:.15rem; }
@media (prefers-reduced-motion:reduce) {
  .appnav, .appnav h1, .appnav .tag { transition:none; }
}
"""

# The active tab is often off-screen on a phone -- "WebUI" is seventh. Without this the row
# looks like it starts at "Apps" no matter where you are.
JS = """
<script>(function(){
var nav=document.querySelector('.appnav');if(!nav)return;
var t=nav.querySelector('.tabs a.on');
if(t&&t.scrollIntoView){t.scrollIntoView({inline:'center',block:'nearest'});}
/* IntersectionObserver on a 1px sentinel above the bar is how you detect "is it stuck"
   without a scroll handler firing on every frame. The sentinel scrolls out of view exactly
   when the sticky bar reaches the top. */
var s=document.createElement('div');
s.style.cssText='position:absolute;top:0;height:1px;width:1px;';
nav.parentNode.insertBefore(s,nav);
if(!('IntersectionObserver' in window))return;
new IntersectionObserver(function(e){
  nav.classList.toggle('stuck',!e[0].isIntersecting);
},{threshold:0}).observe(s);
})();</script>
"""


# RENAMED 2026-09-14 at Jas's request: "I want to rename Party Planning Comittee to
# HiveMind Hub." The old name is kept nowhere in the UI. It survives in workspace/tasks/
# and in finished session records, which are HISTORY -- renaming a past task would
# falsify what was decided and when.
# THE CHAT BUTTON (Jas, 2026-10-12, master plan J-A): on every page that draws this header, a 💬
# button opens the Console's latest thread in a panel; a message typed there is POSTed to the
# Console's /latest with this page's address and visible text, so the model knows what Jas is
# looking at. Narrow screens: a bottom sheet. Wide (the unfolded Fold, >= 840 px): the right half,
# the page on the left. Inside the panel's own frame the button is not drawn (no nesting).
CHAT = f"""<style>
#hubchat-btn{{position:fixed;right:16px;bottom:16px;z-index:9999;width:56px;height:56px;border-radius:50%;
 border:0;background:#c45a2c;color:#fff;font-size:26px;box-shadow:0 4px 12px rgba(0,0,0,.3)}}
#hubchat{{position:fixed;z-index:9998;background:#1b1b1f;display:none;flex-direction:column;
 left:0;right:0;bottom:0;height:85vh;border-top:2px solid #c45a2c}}
#hubchat iframe{{flex:1;border:0;width:100%;background:#fff}}
#hubchat form{{display:flex;gap:6px;padding:8px}}
#hubchat textarea{{flex:1;min-height:44px;font-size:16px}}
#hubchat button{{padding:0 14px}}
body.hubchat-open #hubchat{{display:flex}}
@media (min-width:840px){{#hubchat{{top:0;left:auto;width:50vw;height:100vh;border-top:0;border-left:2px solid #c45a2c}}
 body.hubchat-open{{margin-right:50vw}}}}
</style>
<div id="hubchat"><iframe name="hubchat-frame" id="hubchat-frame" title="Chat"></iframe>
<form method="post" action="{CONSOLE}latest" target="hubchat-frame" id="hubchat-form">
<input type="hidden" name="page"><input type="hidden" name="page_text">
<textarea name="text" placeholder="Ask about this page..."></textarea><button>Send</button></form></div>
<button id="hubchat-btn" aria-label="Chat">&#128172;</button>
<script>(function(){{
if(window.self!==window.top){{var b=document.getElementById('hubchat-btn');if(b)b.remove();return;}}
var btn=document.getElementById('hubchat-btn'),fr=document.getElementById('hubchat-frame'),f=document.getElementById('hubchat-form');
btn.onclick=function(){{var open=document.body.classList.toggle('hubchat-open');if(open&&!fr.src)fr.src='{CONSOLE}';}};
f.onsubmit=function(){{var p=document.getElementById('hubchat');
 f.page.value=location.href+' ('+document.title+')';
 var t=document.body.innerText;var ct=p.innerText;f.page_text.value=t.replace(ct,'');
 setTimeout(function(){{f.text.value='';}},50);}};
}})();</script>"""


def header(active="", subtitle="", title="HiveMind Hub"):
    """The full header block: gradient bar, title, per-app subtitle, scrolling tabs.

    `subtitle` is where an app puts what is its own -- the Console's model name and its
    settings links, chatui's chat/all-models switch. It is inserted as HTML, so callers
    escape their own text.
    """
    import html as _h
    tabs = "".join(
        f'<a class="{"on" if key == active else ""}" href="{href}">{_h.escape(label)}</a>'
        for key, href, label in SECTIONS)
    tag = f'<div class="tag">{subtitle}</div>' if subtitle else ""
    return (f'<header class="appnav"><div class="navwrap">'
            f'<h1><span class="em">&#127881;</span> {_h.escape(title)}</h1>'
            f'{tag}<div class="tabs">{tabs}</div></div></header>{JS}{CHAT}')
