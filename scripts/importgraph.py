#!/usr/bin/env python3
"""What does this script reach, by import, inside this repository?

Built for `t235`: the harness inventory finds scripts that call a model by grepping for
`11434`, `ollama` and `/api/chat`, so it cannot see `scope_queue.py`, which calls
`deliberate.deliberate()` instead. A signature sweep over direct callers reads as a complete
audit and is not one -- ChatGPT's "how do you know there aren't 37?" and Z AI's "inventory the
CONSUMERS, not just the callers".

Its own module because the first attempt asked one brief to rewrite the inventory whole. That
burned 58 minutes and failed twice at exactly 16000 output tokens, which `coder.py` reported
as "unusable" because it does not read `done_reason` (`t238`).

**The contract lives in `scripts/tests/test_importgraph.py` and is enforced there, not here.**
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SEARCH_DIRS = [
    ROOT / "scripts",
    ROOT / "projects" / "local-inference",
]


def _resolve_module(dotted_name):
    """Resolve a dotted module name to a repo-relative .py path string, or None."""
    parts = dotted_name.split(".")
    for base in SEARCH_DIRS:
        candidate = base / Path(*parts)
        mod_file = candidate.with_suffix(".py")
        if mod_file.is_file():
            return mod_file.relative_to(ROOT).as_posix()
        pkg_init = candidate / "__init__.py"
        if pkg_init.is_file():
            return pkg_init.relative_to(ROOT).as_posix()
    return None


def _extract_module_names(tree):
    """Yield every candidate module name from import statements in an AST."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                for i in range(1, len(parts) + 1):
                    yield ".".join(parts[:i])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                parts = node.module.split(".")
                for i in range(1, len(parts) + 1):
                    yield ".".join(parts[:i])
                for alias in node.names:
                    if alias.name != "*":
                        yield node.module + "." + alias.name


def imports_of(path):
    """Parse the file at `path` (repo-relative) and return the set of repo-relative
    .py paths that its import statements resolve to inside this repository."""
    full_path = ROOT / path
    if not full_path.is_file():
        return set()
    try:
        source = full_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()
    result = set()
    for mod_name in _extract_module_names(tree):
        resolved = _resolve_module(mod_name)
        if resolved is not None and resolved != path:
            result.add(resolved)
    return result


def reaches(path):
    """Follow imports_of transitively from `path` and return every repo file
    reachable. Never includes `path` itself. Terminates on cycles.
    Returns an empty set for a path that does not exist."""
    if not (ROOT / path).is_file():
        return set()
    visited = set()
    queue = [path]
    while queue:
        current = queue.pop()
        for dep in imports_of(current):
            if dep not in visited and dep != path:
                visited.add(dep)
                queue.append(dep)
    return visited
