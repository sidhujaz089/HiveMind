#!/usr/bin/env python3
"""workspace-mcp — a thin MCP shell over core.Workspace.

Deliberately thin. Everything worth testing lives in core.py and is exercised by
scripts/tests/test_workspace_mcp.py without a transport; this file only maps tools
onto that and starts stdio.

    .venv/bin/python -m workspace_mcp.server            # stdio, for a local client
    WORKSPACE_MCP_CALLER=chat .venv/bin/python -m workspace_mcp.server

STDIO ONLY, ON PURPOSE. Serving this over the network is a separate decision that
needs Jas's go-ahead (Tailscale Funnel), and there is no code path here that opens
a socket. Adding one should be a visible commit, not a flag.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from workspace_mcp.core import (  # noqa: E402
    Workspace, Denied, STDIO_TOOLS, HTTP_TOOLS,
)
from workspace_mcp import auth, core, oauth  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CALLER = os.environ.get("WORKSPACE_MCP_CALLER", "unknown")

ws = Workspace(REPO_ROOT, caller=CALLER)

app = MCPServer(
    name="workspace-mcp",
    instructions=(
        "The ai-workspace repository. Read AGENTS.md first — it is the contract and it "
        "applies to you. Every call you make here is logged server-side to "
        "workspace/mcp-calls.jsonl, which is written by this server rather than by you. "
        "chat/ is served READ-ONLY -- it is the corpus and your own memory, so read and "
        "search it freely; writes there are refused as path-read-only and inbox_post is "
        "how you ask for a change. workspace/private/ is not served at all."
    ),
)


def _guard(fn, *args, **kwargs):
    """Turn a refusal into a message the caller can act on, rather than a stack trace.

    The rule name is preserved verbatim, because a refusal that does not say WHICH rule
    it broke is worse than a silent one -- it puts a wrong reason into the record the
    decision gets made from (D-21, amendment 4)."""
    try:
        return fn(*args, **kwargs)
    except Denied as e:
        return {"refused": True, "rule": e.rule, "detail": e.detail}


@app.tool(description="Read a UTF-8 text file from the workspace.")
def read(path: str) -> dict:
    return _guard(ws.read, path)


@app.tool(description="Write a file, then re-read it and report the bytes and SHA-256 "
                      "actually on disk. 'Wrote it' is a measurement, not a claim.")
def write(path: str, content: str) -> dict:
    return _guard(ws.write, path, content)


@app.tool(description="Replace an exact anchor that must occur EXACTLY once. Refuses on "
                      "zero matches (the file is not what you think) or more than one "
                      "(the edit is ambiguous).")
def replace(path: str, old: str, new: str) -> dict:
    return _guard(ws.replace, path, old, new)


@app.tool(description="Append text to a file under workspace/ (created if absent). "
                      "Append-only, no read-modify-write. The server records arrival order "
                      "and time -- do not send sequence numbers or totals. 256 KB per call.")
def append(path: str, text: str) -> dict:
    return _guard(ws.append, path, text)


@app.tool(description="Recall facts the local agents have learned about this workspace's "
                      "tooling. Hybrid retrieval: exact-token matching for identifiers like "
                      "L-114 or apply_ops, plus embeddings for paraphrase, fused by "
                      "reciprocal rank. Each result carries a `see` pointer to the document "
                      "that holds the full reasoning -- follow it rather than trusting the "
                      "one-line summary.")
def recall(query: str, limit: int = 6) -> dict:
    """Query the shared memory. Reads only; changes nothing."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "memory", str(Path(REPO_ROOT) / "scripts" / "memory.py"))
    mem = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mem)
    hits = mem.recall(query, limit=limit)
    return {"query": query, "count": len(hits),
            "results": [{"id": h.get("id"), "fact": h.get("fact"),
                         "see": h.get("see"), "tags": h.get("tags")} for h in hits]}


@app.tool(description="Where a function, class or constant is defined in this repository, and which "
                      "files reference it -- read from the AST index, not a text search, so `foo` "
                      "does not match `foobar`. Use it before editing something with callers.")
def symindex_find(name: str) -> dict:
    return _guard(ws.symindex_find, name)


@app.tool(description="Find the skill that covers a kind of task, before starting it. Same hybrid "
                      "retrieval as `recall` (exact identifiers plus embeddings), skills only. Each "
                      "result's `see` points at the SKILL.md to read in full.")
def skills_find(query: str, limit: int = 5) -> dict:
    return _guard(ws.skills_find, query, limit)


@app.tool(description="Traverse the workspace's declared relationships: what enforces a "
                      "lesson, whether it has been superseded, what a task's runs and "
                      "verdicts actually were, and which suites must fail if a file changes. "
                      "Every edge is PARSED from syntax the documents already carry -- "
                      "`ENFORCEMENT:`, `[SUPERSEDED by ...]`, a mutation naming its suite -- "
                      "so it can hold a stale edge but cannot invent one.")
def why(node: str, kind: str = "chain") -> dict:
    """`chain` for a lesson, `impact` for a file, `history` for a task, `neighbours` for any."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "graph", str(Path(REPO_ROOT) / "scripts" / "graph.py"))
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    edges = g.load()
    if kind == "impact":
        return g.impact(node, edges)
    if kind == "history":
        return {"task": node, "rows": g.history(node, edges)}
    if kind == "neighbours":
        return {"node": node, "edges": g.neighbours(node, edges)}
    return g.chain(node, edges)


@app.tool(description="Case-insensitive substring search. Defaults to ALL text files "
                      "including the chat/ corpus. The response counts what was "
                      "skipped -- dependency trees, non-text files, and the frozen "
                      "chat/export/raw/ dump -- so an empty result can be told apart "
                      "from a search that did not look. Pass an explicit glob to "
                      "reach the frozen export.")
def search(query: str, glob: str = "**/*") -> dict:
    # `core.py` was fixed by t059 and THIS layer was not, so every real search Chat ran for
    # five days used **/*.md and could not see a single .txt -- all three State Ledgers,
    # PROTOCOL.txt, and the whole chat/empty/files-only/ tree. The description one line
    # above already promised "ALL text files including the chat/ corpus". The prose was
    # right and the code was wrong, in the same file, one line apart.
    # Found by Chat on 2026-08-25, not by any check here: the mutation covering this
    # default pointed at core.py only, so it went on passing.
    return _guard(ws.search, query, glob)


@app.tool(description="Post a message to workspace/inbox/. The server generates the "
                      "filename; callers do not choose paths.")
def inbox_post(subject: str, body: str) -> dict:
    return _guard(ws.inbox_post, subject, body)


@app.tool(description="Hand a change to the local coding agent. It works in a SANDBOX: "
                      "never the working tree, never a commit, never main. It keeps the "
                      "change only if the target suite passes and no other suite broke, and "
                      "delivers a branch, a diff and the test output for Jas to merge. "
                      "Give a brief and the file(s) to work on. Returns immediately with a "
                      "pid -- it is dispatched, not finished; call status to see it.")
def dispatch_coder(brief: str, files: list[str], target: str = "") -> dict:
    return _guard(ws.dispatch_coder, brief, files, target)


@app.tool(description="List messages waiting in workspace/inbox/.")
def inbox_list() -> dict:
    return _guard(ws.inbox_list)


@app.tool(description="What the local models are doing RIGHT NOW: builds today (done/total "
                      "and an ETA), each running job's pid, model, brief, elapsed time and "
                      "recent tokens/s, the GPU and the resident models. Read-only; the same "
                      "report as scripts/lane_status.py.")
def live_status() -> dict:
    return _guard(ws.live_status)


@app.tool(description="Every open task assigned to Jas (or to `who`: production, claude, or a "
                      "Models-tab role), in queue order -- id, title, status. One call, no limit.")
def my_tasks(who: str = "jas") -> dict:
    return _guard(ws.my_tasks, who)


@app.tool(description="One task by number (t662 or 662): title, status, who it is assigned to, "
                      "and its whole body including every note.")
def task_get(task_id: str) -> dict:
    return _guard(ws.task_get, task_id)


@app.tool(description="Add a note to a task's Notes section, through the task store the Hub uses. "
                      "Signed 'Claude - Chat' and marked '(via Chat, relaying Jas)' -- a note in "
                      "Jas's own name can only come from the Hub form (L-091).")
def task_note(task_id: str, text: str) -> dict:
    return _guard(ws.task_note, task_id, text)


@app.tool(description="Assign a task: production (the models build it), jas, claude, or a "
                      "Models-tab role. Goes through the task store, so the queue stays in step.")
def task_assign(task_id: str, who: str) -> dict:
    return _guard(ws.task_assign, task_id, who)


@app.tool(description="The whole workspace in one file: every project's state and next "
                      "step, from workspace/PROJECT-INDEX.md.")
def status() -> dict:
    return _guard(ws.status)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    ap.add_argument("--host", default="127.0.0.1",
                    help="Ignored for stdio. LOOPBACK ONLY -- see the check below.")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--tools", default=None,
                    help="comma-separated tools to serve. Default: the historic HTTP set. "
                         "Tools not listed are REMOVED from the registry, not permission-"
                         "checked -- an unregistered tool cannot be called, argued for, or "
                         "reached by a prompt injection.")
    ap.add_argument("--token-file", default=None,
                    help=f"Bearer token file. Default {auth.TOKEN_FILE}")
    ap.add_argument("--issuer", default="https://mcp.example.invalid",
                    help="Public base URL this server is reached at.")
    ap.add_argument("--rotate-credentials", action="store_true",
                    help="Replace the bearer token AND the OAuth client, and revoke every "
                         "issued token. Prints the new values once. Breaks the connector "
                         "until it is re-added in claude.ai.")
    ap.add_argument("--show-client", action="store_true",
                    help="Print the OAuth client_id and client_secret, then exit.")
    ap.add_argument("--new-token", action="store_true",
                    help="Generate a token, write it 0600, print it once, and exit.")
    args = ap.parse_args()

    if args.rotate_credentials:
        # Everything at once, on purpose. Rotating the client secret while leaving issued
        # tokens valid would look like a rotation and change nothing for anyone already
        # holding a token -- the failure mode being guarded against.
        from workspace_mcp.oauth import CLIENT_FILE
        store = oauth.Store()
        store.revoke_all()
        if CLIENT_FILE.is_file():
            CLIENT_FILE.unlink()
        new_client = oauth.load_or_create_client()

        token_path = Path(args.token_file) if args.token_file else auth.TOKEN_FILE
        token_path.parent.mkdir(parents=True, exist_ok=True)
        new_token = auth.generate_token()
        token_path.touch(mode=0o600, exist_ok=True)
        token_path.chmod(0o600)
        token_path.write_text(new_token + "\n", encoding="utf-8")

        print("Rotated. Every previously issued token is now invalid.\n")
        print(f"  client_id     {new_client['client_id']}")
        print(f"  client_secret {new_client['client_secret']}")
        print(f"  bearer token  {new_token}")
        print("\nRestart the service, then DELETE and re-add the connector in claude.ai")
        print("with the new client_id and client_secret:")
        print("    systemctl --user restart workspace-mcp")
        print("\nEditing the existing connector is not enough -- it holds an access token")
        print("that no longer validates, and will keep presenting it.")
        return 0

    if args.show_client:
        c = oauth.load_or_create_client()
        print(f"client_id     {c['client_id']}")
        print(f"client_secret {c['client_secret']}")
        return 0

    if args.new_token:
        path = Path(args.token_file) if args.token_file else auth.TOKEN_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        token = auth.generate_token()
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_text(token + "\n", encoding="utf-8")
        print(f"wrote {path} (0600)")
        print(token)
        print("\nThis is the only time it is printed. Paste it into the claude.ai "
              "connector\nconfiguration; it is not recoverable from here without "
              "reading the file.")
        return 0

    if args.transport == "stdio":
        app.run(transport="stdio")
        return 0

    # --- http: two refusals before a socket is opened -------------------------------
    #
    # 1. Never bind a routable interface. The tunnel connects to loopback; anything that
    #    reaches this process is meant to have come through cloudflared and the edge WAF.
    #    Binding 0.0.0.0 would put the workspace on the LAN and, behind a misconfigured
    #    router, on the internet -- silently, with everything else still looking correct.
    if args.host not in ("127.0.0.1", "::1", "localhost"):
        print(f"refusing to bind {args.host}: http transport is loopback-only.",
              file=sys.stderr)
        print("cloudflared forwards to 127.0.0.1; nothing else should reach this.",
              file=sys.stderr)
        return 2

    # 2. Reduce the toolset before serving. Removal, not permission-checking: a tool that
    #    is not registered cannot be called, argued for, or reached by a prompt injection.
    # --tools selects the served set explicitly; without it, the historic HTTP_TOOLS set.
    # This is what lets a SECOND instance serve a narrower set to a different client: the
    # registry is per-server, not per-token, so a narrower door needs its own process.
    if args.tools:
        want = tuple(t.strip() for t in args.tools.split(",") if t.strip())
        unknown = [t for t in want if t not in STDIO_TOOLS]
        if unknown:
            print(f"unknown tool(s) in --tools: {', '.join(unknown)}", file=sys.stderr)
            print(f"choose from: {', '.join(STDIO_TOOLS)}", file=sys.stderr)
            return 2
        keep = want
    else:
        keep = HTTP_TOOLS
    removed = [t for t in STDIO_TOOLS if t not in keep]
    for name in removed:
        app.remove_tool(name)
    # Tell the workspace what SURVIVED removal, so status() reports the registry rather than
    # the constant it was reduced from. Chat asked for exactly this distinction.
    ws.served = tuple(t for t in STDIO_TOOLS if t not in removed)
    print(f"http transport: withheld {', '.join(removed) or 'nothing'}", file=sys.stderr)
    print(f"serving {len(ws.served)} tools on {args.host}:{args.port}: "
          f"{', '.join(ws.served)}", file=sys.stderr)

    # 3. Refuse to serve at all without a usable token. A server that starts with no
    #    credential and answers everyone is the failure this whole layer exists to
    #    prevent -- and from the outside it looks completely healthy.
    try:
        expected = auth.load_token(args.token_file)
    except auth.AuthError as e:
        print(f"refusing to serve over http: {e}", file=sys.stderr)
        print("generate one with:  python3 -m workspace_mcp.server --new-token",
              file=sys.stderr)
        return 2
    print(f"auth: bearer token loaded ({len(expected)} chars)", file=sys.stderr)

    # --- OAuth, sized to what claude.ai actually requests ---------------------------
    client = oauth.load_or_create_client()
    store = oauth.Store()
    issuer = args.issuer.rstrip("/")
    print(f"oauth: client_id {client['client_id']}", file=sys.stderr)

    from starlette.responses import JSONResponse, RedirectResponse, Response
    from urllib.parse import urlencode, parse_qs

    # DNS-rebinding protection, kept ON and told the truth rather than switched off.
    #
    # The SDK validates the Host header because a server bound to loopback can otherwise
    # be reached by a hostile page that resolves its own domain to 127.0.0.1 -- the
    # browser sends the attacker's Host and the request looks local. Real protection,
    # worth keeping.
    #
    # It rejected mcp.example.invalid because Cloudflare correctly forwards the ORIGINAL
    # Host, which is not a loopback name. The fix is to declare the hostname this server
    # is legitimately reached at, not to disable the check: an allowlist of one real name
    # still refuses every name an attacker could invent.
    from mcp.server.transport_security import TransportSecuritySettings
    from urllib.parse import urlparse

    public_host = urlparse(issuer).netloc
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[public_host, f"127.0.0.1:{args.port}", f"localhost:{args.port}",
                       "127.0.0.1", "localhost"],
        allowed_origins=[issuer, "https://claude.ai"],
    )
    print(f"transport security: host allowlist {security.allowed_hosts}", file=sys.stderr)

    inner = app.streamable_http_app(transport_security=security)

    # Discovery must be PUBLIC. It is how a client learns how to authenticate, so
    # guarding it produces a 401 the client cannot act on -- which is exactly what
    # happened on the first connector attempt: five probes, five refusals, no way
    # forward. Observed, then fixed.
    PUBLIC_PATHS = {
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-authorization-server/mcp",
        "/authorize",
        "/token",
    }

    async def body_of(receive):
        chunks, more = b"", True
        while more:
            msg = await receive()
            chunks += msg.get("body", b"")
            more = msg.get("more_body", False)
        return chunks

    async def handle_oauth(scope, receive, send, path, headers):
        client_ip = (scope.get("client") or ["?"])[0]
        query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))

        def one(k):
            v = query.get(k, [None])
            return v[0] if v else None

        if path.startswith("/.well-known/oauth-protected-resource"):
            ws.log("oauth", path, "ok", extra={"client": client_ip})
            await JSONResponse(oauth.protected_resource_metadata(issuer))(scope, receive, send)
            return True

        if path.startswith("/.well-known/oauth-authorization-server"):
            ws.log("oauth", path, "ok", extra={"client": client_ip})
            await JSONResponse(oauth.authorization_server_metadata(issuer))(scope, receive, send)
            return True

        if path == "/authorize":
            # Auto-approve: one user, one client, one server. A consent page here would
            # be Jas clicking yes to himself. client_id is still validated, and the code
            # is bound to the challenge, the client and the redirect_uri.
            redirect_uri = one("redirect_uri") or ""
            state = one("state") or ""
            try:
                if one("client_id") != client["client_id"]:
                    raise oauth.OAuthError("unauthorized_client", "client-id-unknown")
                code = store.issue_code(client["client_id"], redirect_uri,
                                        one("code_challenge") or "",
                                        one("code_challenge_method") or "S256")
            except oauth.OAuthError as e:
                ws.log("oauth", path, "refused", e.rule, extra={"client": client_ip})
                await JSONResponse({"error": e.code, "error_description": e.rule},
                                   status_code=400)(scope, receive, send)
                return True
            ws.log("oauth", path, "ok", "code-issued", extra={"client": client_ip})
            sep = "&" if "?" in redirect_uri else "?"
            target = f"{redirect_uri}{sep}{urlencode({'code': code, 'state': state})}"
            await RedirectResponse(target, status_code=302)(scope, receive, send)
            return True

        if path == "/token":
            form = parse_qs((await body_of(receive)).decode("utf-8", "replace"))

            def f(k):
                v = form.get(k, [None])
                return v[0] if v else None

            # Secret may arrive in the body or as HTTP Basic; both are advertised.
            secret = f("client_secret")
            cid = f("client_id")
            basic = headers.get("authorization") or headers.get("Authorization")
            if not secret and basic and basic.lower().startswith("basic "):
                import base64 as _b64
                try:
                    decoded = _b64.b64decode(basic.split(None, 1)[1]).decode("utf-8")
                    cid, secret = decoded.split(":", 1)
                except Exception:  # noqa: BLE001 - malformed header is just a refusal
                    cid, secret = cid, None
            try:
                if cid != client["client_id"]:
                    raise oauth.OAuthError("invalid_client", "client-id-unknown")
                oauth.check_client_secret(secret, client["client_secret"])
                store.redeem_code(f("code"), f("code_verifier"), cid,
                                  f("redirect_uri") or "")
                token, ttl = store.issue_token(cid)
            except oauth.OAuthError as e:
                ws.log("oauth", path, "refused", e.rule, extra={"client": client_ip})
                await JSONResponse({"error": e.code, "error_description": e.rule},
                                   status_code=400)(scope, receive, send)
                return True
            ws.log("oauth", path, "ok", "token-issued", extra={"client": client_ip})
            await JSONResponse({"access_token": token, "token_type": "Bearer",
                                "expires_in": ttl})(scope, receive, send)
            return True

        return False

    async def guarded(scope, receive, send):
        if scope["type"] != "http":
            await inner(scope, receive, send)
            return
        headers = {k.decode("latin-1"): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        path = scope.get("path", "?")

        if path in PUBLIC_PATHS or path.startswith("/.well-known/"):
            if await handle_oauth(scope, receive, send, path, headers):
                return

        # Everything else needs a token: either one this server issued, or the static
        # admin token used locally.
        try:
            presented = auth.extract_bearer(headers)
        except auth.AuthError as e:
            ws.log("http-auth", path, "refused", e.rule, extra={"client": (scope.get("client") or ["?"])[0]})
            await unauthorized(scope, receive, send)
            return

        import hmac as _hmac
        client_ip = (scope.get("client") or ["?"])[0]
        issued_to = store.identify_token(presented)
        is_admin = _hmac.compare_digest(presented, expected)
        if not (issued_to or is_admin):
            ws.log("http-auth", path, "refused", "token-mismatch", extra={"client": client_ip})
            await unauthorized(scope, receive, send)
            return

        # G11. Set here and nowhere else: this is the last point at which the credential is
        # known. The token is reset in `finally` so the value cannot outlive the request even
        # if the SDK reuses the task -- belt and braces, since contextvars are task-scoped
        # anyway, but the failure this guards against is silent misattribution.
        ctx_token = core.set_call_context(
            client=client_ip,
            via=(f"oauth:{issued_to}" if issued_to else "admin-bearer"),
        )
        try:
            await inner(scope, receive, send)
        finally:
            core.CALL_CONTEXT.reset(ctx_token)

    async def unauthorized(scope, receive, send):
        # RFC 9728: point the client at the metadata that tells it how to authenticate.
        # Without this header a 401 is a dead end rather than the start of a flow.
        await JSONResponse(
            {"error": "unauthorized"}, status_code=401,
            headers={"WWW-Authenticate":
                     f'Bearer resource_metadata="{issuer}/.well-known/oauth-protected-resource"'},
        )(scope, receive, send)

    import uvicorn
    uvicorn.run(guarded, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
