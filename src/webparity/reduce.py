# SPDX-License-Identifier: BSD-2-Clause
"""``webparity reduce``: shrink a page to a small test case that still shows a finding.

The finding is a *predicate*: a script (or a test case's ``#out``) run through the probe in some browsers, and a
condition on the results, for example "Ladybird and Chromium return different values" or "Ladybird crashes".
Every candidate is written to a fresh file and probed; a candidate is kept when the predicate still holds.

The reducers run in this order, and the whole sequence is repeated while it still makes progress:

1. ``scripts``: remove whole ``<script>`` elements, inline local external scripts, then remove top-level statements
   (chunks split at top-level ``;`` and line breaks);
2. ``stylesheets``: inline local ``<link rel=stylesheet>`` files into ``<style>``;
3. ``dom``: remove nodes level by level (hierarchical delta debugging), then unwrap elements (keep their children);
4. ``css-rules``: remove rules in ``<style>``, top-level rules first, then rules inside ``@media`` and friends;
5. ``declarations``: remove declarations in ``<style>`` rules and ``style=""`` attributes;
6. ``attributes``: remove attributes;
7. ``text``: shorten text nodes.

Each reducer is a pure function ``reduce_x(html, test, ...) -> html`` where ``test(html) -> bool``; it only returns
documents for which ``test`` returned true (or its input). The parsers are approximate on purpose: a wrong guess about
the structure only makes a reduction step less effective, never wrong, because every step is checked by the predicate.

A source is a local HTML file (candidates are written next to it, so relative resources keep working) or a visit
bundle: its saved DOM (``<browser>-dom.html.gz``) plus the resources in ``recording.warc.gz``, written to the work
directory with the page's URLs rewritten to the local copies, so the test case runs offline.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html as html_lib
import html.parser
import io
import os
import posixpath
import re
import sys
import time
import urllib.parse
import zlib
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

PASSES = ("scripts", "stylesheets", "dom", "css-rules", "declarations", "attributes", "text")
CANDIDATE_PREFIX = "_webparity-candidate-"


def _enc(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def _size(text: str) -> int:
    return len(_enc(text))


# --- delta debugging ---------------------------------------------------------------------------------------------


class BudgetExhausted(Exception):
    """Raised by a test when the test budget is used up; ``ddmin`` stops and returns what it has."""


def ddmin(
    items: Sequence[T],
    test: Callable[[list[T]], bool],
    *,
    max_tests: int | None = None,
    cache: dict | None = None,
) -> list[T]:
    """Classic delta debugging (Zeller): a 1-minimal subset of ``items`` for which ``test`` is true.

    ``test(items)`` is assumed to be true. The subsets tested are cached (by index), so no subset is tested twice;
    ``max_tests`` caps the number of calls to ``test``. When the budget runs out (here, or by the test raising
    ``BudgetExhausted``) the smallest subset found so far is returned. The result keeps the original order.
    The empty subset is tried first, and complements (removing a chunk) are tried before the chunks themselves.
    """
    items = list(items)
    cache = {} if cache is None else cache
    calls = 0

    def run(subset: tuple[int, ...]) -> bool:
        nonlocal calls
        if subset in cache:
            return cache[subset]
        if max_tests is not None and calls >= max_tests:
            raise BudgetExhausted
        calls += 1
        result = bool(test([items[i] for i in subset]))
        cache[subset] = result
        return result

    current = tuple(range(len(items)))
    try:
        if current and run(()):
            return []
        n = 2
        while len(current) >= 2:
            size = -(-len(current) // n)
            chunks = [current[i : i + size] for i in range(0, len(current), size)]
            reduced = False
            for chunk in chunks:
                drop = set(chunk)
                complement = tuple(i for i in current if i not in drop)
                if complement and run(complement):
                    current, n, reduced = complement, max(n - 1, 2), True
                    break
            if not reduced and len(chunks) > 2:
                for chunk in chunks:
                    if run(chunk):
                        current, n, reduced = chunk, 2, True
                        break
            if not reduced:
                if n >= len(current):
                    break
                n = min(len(current), n * 2)
    except BudgetExhausted:
        pass
    return [items[i] for i in current]


# --- HTML document model -----------------------------------------------------------------------------------------

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
TRANSPARENT = {"html", "head", "body"}
RAW_TEXT = {"script", "style", "textarea", "title", "xmp", "noscript"}
# Start tags that implicitly close an open element of these types (a small part of the HTML tree builder).
IMPLIED_CLOSE = {
    "li": {"li"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "option": {"option"},
    "tr": {"tr", "td", "th"},
    "td": {"td", "th"},
    "th": {"td", "th"},
    "thead": {"thead", "tbody", "tfoot", "tr", "td", "th"},
    "tbody": {"thead", "tbody", "tfoot", "tr", "td", "th"},
    "tfoot": {"thead", "tbody", "tfoot", "tr", "td", "th"},
}
CLOSES_P = set(
    "address article aside blockquote details div dl fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header "
    "hr main menu nav ol p pre section table ul".split()
)


@dataclass(eq=False)
class Node:
    """A node with its exact source text: ``raw`` is the whole node, or an element's start tag (``end`` its end tag)."""

    kind: str  # "root", "element", "text", "comment", "decl" (doctype etc.), "other" (stray end tags, PIs)
    raw: str = ""
    tag: str = ""
    end: str = ""
    children: list[Node] = field(default_factory=list)
    parent: Node | None = None
    id: int = 0


class _Tokenizer(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.events: list[tuple[int, str, str, bool]] = []  # (line, column), kind, tag, self-closing

    def _add(self, kind: str, tag: str = "", closed: bool = False) -> None:
        self.events.append((*self.getpos(), kind, tag, closed))

    def handle_starttag(self, tag, attrs):
        self._add("start", tag)

    def handle_startendtag(self, tag, attrs):
        self._add("start", tag, True)

    def handle_endtag(self, tag):
        self._add("end", tag)

    def handle_data(self, data):
        self._add("text")

    def handle_entityref(self, name):
        self._add("text")

    def handle_charref(self, name):
        self._add("text")

    def handle_comment(self, data):
        self._add("comment")

    def handle_decl(self, decl):
        self._add("decl")

    def unknown_decl(self, data):
        self._add("other")

    def handle_pi(self, data):
        self._add("other")


class Doc:
    """An HTML document as a tree of source slices; ``render()`` reproduces the source exactly."""

    def __init__(self, text: str):
        self.text = text
        tokenizer = _Tokenizer()
        tokenizer.feed(text)
        tokenizer.close()
        line_starts = [0]
        for m in re.finditer("\n", text):
            line_starts.append(m.end())
        starts = [(line_starts[line - 1] + col, kind, tag, closed) for line, col, kind, tag, closed in tokenizer.events]
        tokens: list[tuple[str, str, str, bool]] = []
        if not starts or starts[0][0] > 0:
            starts.insert(0, (0, "text", "", False))
        for i, (offset, kind, tag, closed) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
            tokens.append((text[offset:end], kind, tag, closed))
        self.root = Node("root")
        stack = [self.root]
        for raw, kind, tag, closed in tokens:
            if not raw and kind != "start":
                continue
            parent = stack[-1]
            if kind == "text":
                if parent.children and parent.children[-1].kind == "text":
                    parent.children[-1].raw += raw
                else:
                    parent.children.append(Node("text", raw, parent=parent))
            elif kind == "start":
                if tag in IMPLIED_CLOSE:
                    while len(stack) > 1 and stack[-1].tag in IMPLIED_CLOSE[tag]:
                        stack.pop()
                elif tag in CLOSES_P and stack[-1].tag == "p":
                    stack.pop()
                parent = stack[-1]
                node = Node("element", raw, tag, parent=parent)
                parent.children.append(node)
                foreign = tag in ("svg", "math") or any(n.tag in ("svg", "math") for n in stack)
                if tag not in VOID and not (closed and foreign):
                    stack.append(node)
            elif kind == "end":
                for i in range(len(stack) - 1, 0, -1):
                    if stack[i].tag == tag:
                        stack[i].end = raw
                        del stack[i:]
                        break
                else:
                    parent.children.append(Node("other", raw, tag, parent=parent))
            else:
                parent.children.append(Node(kind, raw, parent=parent))
        self.nodes: list[Node] = []
        todo = [self.root]
        while todo:
            node = todo.pop()
            node.id = len(self.nodes)
            self.nodes.append(node)
            todo.extend(reversed(node.children))

    def render(
        self,
        removed: Iterable[int] = (),
        unwrapped: Iterable[int] = (),
        inner: dict[int, str] | None = None,
        start: dict[int, str] | None = None,
        replace: dict[int, str] | None = None,
    ) -> str:
        """The document with nodes removed, elements unwrapped, element contents (``inner``), start tags or whole
        nodes (``replace``) replaced; all keyed by node id."""
        removed, unwrapped = set(removed), set(unwrapped)
        inner, start, replace = inner or {}, start or {}, replace or {}
        out: list[str] = []
        todo: list[Node | str] = [self.root]
        while todo:
            item = todo.pop()
            if isinstance(item, str):
                out.append(item)
                continue
            node = item
            if node.id in removed:
                continue
            if node.id in replace:
                out.append(replace[node.id])
                continue
            if node.kind == "root" or node.id in unwrapped:
                todo.extend(reversed(node.children))
                continue
            if node.kind != "element":
                out.append(node.raw)
                continue
            out.append(start.get(node.id, node.raw))
            todo.append(node.end)
            if node.id in inner:
                todo.append(inner[node.id])
            else:
                todo.extend(reversed(node.children))
        return "".join(out)

    def subtree_text(self, node: Node) -> str:
        if node.kind != "element":
            return node.raw
        parts = [node.raw]
        todo = list(reversed(node.children))
        while todo:
            n = todo.pop()
            if n.kind == "element":
                parts.append(n.raw)
                todo.append(Node("text", n.end))
                todo.extend(reversed(n.children))
            else:
                parts.append(n.raw)
        parts.append(node.end)
        return "".join(parts)

    def elements(self, *tags: str) -> list[Node]:
        return [n for n in self.nodes if n.kind == "element" and (not tags or n.tag in tags)]

    def text_content(self, node: Node) -> str:
        return "".join(c.raw for c in node.children if c.kind == "text")


_ATTR_RE = re.compile(r"""[\s/]*([^\s/>"'=][^\s/>=]*(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]*))?)""")


def split_start_tag(raw: str) -> tuple[str, list[str], str]:
    """``<tag a=1 b>rest`` → (``<tag``, [``a=1``, ``b``], ``>rest``)."""
    m = re.match(r"<[^\s/>]+", raw)
    if not m:
        return raw, [], ""
    pos, attrs = m.end(), []
    while True:
        a = _ATTR_RE.match(raw, pos)
        if not a:
            break
        attrs.append(a.group(1))
        pos = a.end()
    return m.group(0), attrs, raw[pos:]


def join_start_tag(head: str, attrs: Iterable[str], tail: str) -> str:
    return head + "".join(" " + a for a in attrs) + tail


def attr_name(attr: str) -> str:
    return attr.split("=", 1)[0].strip().lower()


def attr_value(attr: str) -> str | None:
    if "=" not in attr:
        return None
    value = attr.split("=", 1)[1].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return html_lib.unescape(value)


def make_attr(name: str, value: str) -> str:
    return f'{name}="{value.replace("&", "&amp;").replace(chr(34), "&quot;")}"'


def attributes(node: Node) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for a in split_start_tag(node.raw)[1]:
        out.setdefault(attr_name(a), attr_value(a))
    return out


# --- CSS model ---------------------------------------------------------------------------------------------------

GROUP_RULES = (
    "@media",
    "@supports",
    "@layer",
    "@container",
    "@document",
    "@-moz-document",
    "@scope",
    "@starting-style",
)


def _skip_string(text: str, i: int) -> int:
    quote, n = text[i], len(text)
    i += 1
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == quote or c == "\n":
            return i + 1
        i += 1
    return n


def strip_css_comments(text: str) -> str:
    out, i, n, last = [], 0, len(text), 0
    while i < n:
        c = text[i]
        if c in "\"'":
            i = _skip_string(text, i)
        elif c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(text[last:i])
            i = last = j
        else:
            i += 1
    out.append(text[last:])
    return "".join(out)


def _match_brace(text: str, i: int) -> int:
    """Index after the ``}`` matching the ``{`` at ``i``."""
    depth, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            i = _skip_string(text, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _has_block(text: str) -> bool:
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            i = _skip_string(text, i)
            continue
        if c == "{":
            return True
        i += 1
    return False


def split_declarations(body: str) -> list[str]:
    """``a: 1; b: url(x;y)`` → [``a: 1``, ``b: url(x;y)``]."""
    out, depth, start, i, n = [], 0, 0, 0, len(body)
    while i < n:
        c = body[i]
        if c in "\"'":
            i = _skip_string(body, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
        elif c == ";" and depth == 0:
            out.append(body[start:i])
            start = i + 1
        i += 1
    out.append(body[start:])
    return [d.strip() for d in out if d.strip()]


@dataclass(eq=False)
class Rule:
    prelude: str  # stripped
    body: str | None = None  # None for statements such as @import
    children: list[Rule] | None = None  # group rules
    decls: list[str] | None = None  # rules whose body is a declaration list
    rid: int = 0
    head: str = ""  # the prelude as written, with the whitespace before "{"

    @property
    def opening(self) -> str:
        return self.head or self.prelude


def parse_css(text: str, _counter: list[int] | None = None) -> list[Rule]:
    """Rules at the top level of a style sheet; group rules (``@media`` etc.) have children. Comments are dropped."""
    counter = _counter if _counter is not None else [0]
    if _counter is None:
        text = strip_css_comments(text)
    rules: list[Rule] = []
    i, start, n = 0, 0, len(text)

    def add(rule: Rule) -> None:
        rule.rid = counter[0]
        counter[0] += 1
        rules.append(rule)

    while i < n:
        c = text[i]
        if c in "\"'":
            i = _skip_string(text, i)
        elif c == ";":
            statement = text[start : i + 1].strip()
            if statement != ";":
                add(Rule(statement))
            i = start = i + 1
        elif c == "{":
            j = _match_brace(text, i)
            prelude, body = text[start:i].strip(), text[i + 1 : j - 1]
            rule = Rule(prelude, body, head=text[start:i].lstrip())
            add(rule)
            if prelude.lower().startswith(GROUP_RULES) and _has_block(body):
                rule.children = parse_css(body, counter)
            elif not _has_block(body):
                rule.decls = split_declarations(body)
            i = start = j
        elif c == "}":  # a stray closing brace
            i = start = i + 1
        else:
            i += 1
    rest = text[start:].strip()
    if rest:
        add(Rule(rest))
    return rules


def iter_rules(rules: list[Rule]) -> Iterator[Rule]:
    for r in rules:
        yield r
        if r.children:
            yield from iter_rules(r.children)


def serialize_css(rules: list[Rule], removed: set[int] = frozenset(), removed_decls: set | None = None) -> str:
    """The rules without ``removed`` rule ids and ``removed_decls`` (rule id, index) declarations. Group rules left
    empty are dropped, except ``@layer`` (an empty layer block still orders layers)."""
    parts = []
    for r in rules:
        if r.rid in removed:
            continue
        if r.children is not None:
            inner = serialize_css(r.children, removed, removed_decls)
            if not inner.strip() and not r.prelude.lower().startswith("@layer"):
                continue
            parts.append(f"{r.opening}{{\n{inner}\n}}")
        elif r.body is None:
            parts.append(r.prelude)
        elif r.decls is not None and removed_decls is not None:
            body = "; ".join(d for k, d in enumerate(r.decls) if (r.rid, k) not in removed_decls)
            parts.append(f"{r.opening}{{{body}}}")
        else:
            parts.append(f"{r.opening}{{{r.body}}}")
    return "\n".join(parts)


# --- JavaScript chunks -------------------------------------------------------------------------------------------

_REGEX_KEYWORDS = {
    "return",
    "typeof",
    "case",
    "do",
    "else",
    "in",
    "of",
    "new",
    "delete",
    "void",
    "throw",
    "yield",
    "await",
}


def _skip_template(src: str, i: int) -> int:
    n = len(src)
    i += 1
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            return i + 1
        if c == "$" and src.startswith("{", i + 1):
            i += 2
            depth = 1
            while i < n and depth:
                c = src[i]
                if c in "\"'":
                    i = _skip_string(src, i)
                    continue
                if c == "`":
                    i = _skip_template(src, i)
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                i += 1
            continue
        i += 1
    return n


def _skip_regex(src: str, i: int) -> int:
    n, in_class = len(src), False
    i += 1
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "\n":
            return i
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            i += 1
            while i < n and (src[i].isalnum() or src[i] == "_"):
                i += 1
            return i
        i += 1
    return n


def split_js(src: str) -> list[str]:
    """Top-level statements, roughly: ``src`` cut after ``;`` and line breaks outside brackets, strings, comments,
    templates and regular expressions. The chunks concatenate to ``src`` exactly."""
    chunks: list[str] = []
    i, start, depth, n = 0, 0, 0, len(src)
    prev_char, prev_word = "", ""
    while i < n:
        c = src[i]
        if c in "\"'":
            i = _skip_string(src, i)
            prev_char, prev_word = "a", ""
            continue
        if c == "`":
            i = _skip_template(src, i)
            prev_char, prev_word = "a", ""
            continue
        if c == "/" and src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == "/" and (prev_char == "" or prev_char in "(,=:[!&|?{};+-*%<>~^" or prev_word in _REGEX_KEYWORDS):
            i = _skip_regex(src, i)
            prev_char, prev_word = "a", ""
            continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            prev_word, prev_char = src[i:j], "a"
            i = j
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
        if not c.isspace():
            prev_char, prev_word = c, ""
        if depth == 0 and c in ";\n" and src[start : i + 1].strip():
            chunks.append(src[start : i + 1])
            start = i + 1
        i += 1
    rest = src[start:]
    if rest:
        if chunks and not rest.strip():
            chunks[-1] += rest
        else:
            chunks.append(rest)
    return chunks


# --- reducers ----------------------------------------------------------------------------------------------------

Test = Callable[[str], bool]
JS_TYPES = {
    "",
    "module",
    "text/javascript",
    "application/javascript",
    "text/ecmascript",
    "application/ecmascript",
    "application/x-javascript",
}


def _protected(text: str, protect: Sequence[str]) -> bool:
    return any(p and p in text for p in protect)


def _ddmin_units(html_text: str, units: list, render: Callable[[list], str], test: Test) -> tuple[str, list] | None:
    """ddmin over ``units`` (``render(kept)`` builds a candidate): the smallest interesting document and the units it
    keeps, or None when the pass's normalized form (``render(units)``) does not keep the finding."""
    full = render(units)
    if full != html_text:  # the pass normalizes (CSS comments, whitespace): check that the normalized form holds
        try:
            if not test(full):
                return None
        except BudgetExhausted:
            return None
    kept = ddmin(units, lambda subset: test(render(subset)))
    return render(kept), kept


def _reduce_units(html_text: str, units: list, render: Callable[[list], str], test: Test) -> str:
    if not units:
        return html_text
    found = _ddmin_units(html_text, units, render, test)
    return html_text if found is None else found[0]


def reduce_scripts(html_text: str, test: Test, root: Path | None = None, protect: Sequence[str] = ()) -> str:
    """Removes ``<script>`` elements, inlines local classic external scripts, then removes top-level statements."""
    doc = Doc(html_text)
    units = [n.id for n in doc.elements("script") if not _protected(doc.subtree_text(n), protect)]
    html_text = _reduce_units(html_text, units, lambda kept: doc.render(removed=set(units) - set(kept)), test)
    if root is not None:
        html_text = _inline_resources(html_text, test, root, "script")
    doc = Doc(html_text)
    scripts = []
    for n in doc.elements("script"):
        attrs = attributes(n)
        if "src" in attrs or (attrs.get("type") or "").strip().lower() not in JS_TYPES:
            continue
        if _protected(doc.subtree_text(n), protect):
            continue
        scripts.append((n.id, split_js(doc.text_content(n))))
    units = [(sid, k) for sid, chunks in scripts for k in range(len(chunks))]

    def render(kept: list) -> str:
        keep = set(kept)
        return doc.render(inner={sid: "".join(c for k, c in enumerate(ch) if (sid, k) in keep) for sid, ch in scripts})

    return _reduce_units(html_text, units, render, test)


_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)(.*?)\1\s*\)|@import\s+(['"])(.*?)\3""", re.I | re.S)


def _local_file(ref: str, root: Path) -> Path | None:
    """The file under ``root`` that a relative or root-relative reference from a document at ``root`` points to."""
    ref = ref.strip()
    if not ref or _SCHEME_RE.match(ref) or ref.startswith(("//", "#")):
        return None
    path = urllib.parse.unquote(ref.split("#", 1)[0].split("?", 1)[0])
    root = root.resolve()
    target = (root / path.lstrip("/")).resolve()
    if not target.is_file() or root not in target.parents:
        return None
    return target


def _rebase_css(css: str, css_dir: str) -> str:
    """Rewrites relative ``url()`` and ``@import`` references in CSS that lived in ``css_dir`` (relative to the
    document) so they work from the document."""

    def fix(ref: str) -> str:
        if not ref or _SCHEME_RE.match(ref) or ref.startswith(("/", "#")):
            return ref
        return posixpath.normpath(posixpath.join(css_dir, ref)) if css_dir else ref

    def sub(m: re.Match) -> str:
        if m.group(2) is not None:
            return f'url("{fix(m.group(2))}")'
        return f'@import "{fix(m.group(4))}"'

    return _CSS_URL_RE.sub(sub, css)


def _with_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _inline_resources(html_text: str, test: Test, root: Path, kind: str) -> str:
    """Replaces local ``<link rel=stylesheet>`` (kind "style") or classic ``<script src>`` (kind "script") with
    inline elements: all at once if that keeps the finding, else one by one."""
    doc = Doc(html_text)
    replacements: dict[int, str] = {}
    for n in doc.elements("link" if kind == "style" else "script"):
        attrs = attributes(n)
        if kind == "style":
            rel = (attrs.get("rel") or "").lower().split()
            if "stylesheet" not in rel or "alternate" in rel or "disabled" in attrs:
                continue
            path = _local_file(attrs.get("href") or "", root)
            if path is None:
                continue
            css = path.read_bytes().decode("utf-8", "surrogateescape")
            if "</style" in css.lower():
                continue
            css_dir = posixpath.relpath(path.parent.as_posix(), root.resolve().as_posix())
            media = attrs.get("media")
            open_tag = "<style>" if not media else f"<style {make_attr('media', media)}>"
            css = _rebase_css(css, "" if css_dir == "." else css_dir)
            replacements[n.id] = f"{open_tag}\n{_with_newline(css)}</style>"
        else:
            type_ = (attrs.get("type") or "").strip().lower()
            if type_ not in JS_TYPES or type_ == "module" or "async" in attrs or "defer" in attrs:
                continue
            path = _local_file(attrs.get("src") or "", root)
            if path is None:
                continue
            js = path.read_bytes().decode("utf-8", "surrogateescape")
            if "</script" in js.lower():
                continue
            replacements[n.id] = f"<script>\n{_with_newline(js)}</script>"
    if not replacements:
        return html_text
    try:
        candidate = doc.render(replace=replacements)
        if test(candidate):
            return candidate
        accepted: dict[int, str] = {}
        for nid, text in replacements.items():
            candidate = doc.render(replace={**accepted, nid: text})
            if test(candidate):
                accepted[nid] = text
        return doc.render(replace=accepted)
    except BudgetExhausted:
        return html_text


def reduce_stylesheets(html_text: str, test: Test, root: Path) -> str:
    """Inlines local external style sheets into ``<style>`` elements (so the CSS passes can reduce them)."""
    return _inline_resources(html_text, test, root, "style")


def _dom_units(doc: Doc, parents: list[Node], protect: Sequence[str]) -> tuple[list[int], list[Node]]:
    """Removable nodes among the children of ``parents`` (``html``, ``head`` and ``body`` are looked through) and the
    elements among them whose children form the next level."""

    def expand(nodes: list[Node]) -> Iterator[Node]:
        for node in nodes:
            if node.kind == "element" and node.tag in TRANSPARENT:
                yield from expand(node.children)
            else:
                yield node

    units, nxt = [], []
    for node in expand([c for p in parents for c in p.children]):
        if node.kind == "decl" or (node.kind == "text" and not node.raw.strip()):
            continue
        if node.kind == "element" and node.tag not in RAW_TEXT:
            nxt.append(node)
        if not (protect and _protected(doc.subtree_text(node), protect)):
            units.append(node.id)
    return units, nxt


def reduce_dom(html_text: str, test: Test, protect: Sequence[str] = ()) -> str:
    """Removes subtrees level by level (each level is one ddmin over all its nodes), then unwraps elements."""
    depth = 0
    while True:
        doc = Doc(html_text)
        parents: list[Node] = [doc.root]
        units: list[int] = []
        for _ in range(depth + 1):
            units, parents = _dom_units(doc, parents, protect)
        if not units and not parents:
            break
        html_text = _reduce_units(html_text, units, lambda kept, d=doc, u=units: d.render(set(u) - set(kept)), test)
        depth += 1
    doc = Doc(html_text)
    units = [
        n.id
        for n in doc.elements()
        if n.tag not in TRANSPARENT and n.tag not in RAW_TEXT and n.tag != "template" and n.children
        if not _protected(n.raw, protect)
    ]
    return _reduce_units(html_text, units, lambda kept: doc.render(unwrapped=set(units) - set(kept)), test)


def _style_sheets(doc: Doc) -> list[tuple[int, list[Rule]]]:
    counter = [0]
    sheets = []
    for n in doc.elements("style"):
        text = strip_css_comments(doc.text_content(n))
        sheets.append((n.id, parse_css(text, counter)))
    return sheets


def reduce_css_rules(html_text: str, test: Test) -> str:
    """Removes rules in ``<style>`` elements: top-level rules first, then the rules inside surviving group rules."""
    doc = Doc(html_text)
    sheets = _style_sheets(doc)
    removed: set[int] = set()

    def render(level_units: list[int], kept: list[int]) -> str:
        gone = removed | (set(level_units) - set(kept))
        return doc.render(inner={sid: "\n" + serialize_css(rules, gone) + "\n" for sid, rules in sheets})

    level = [r for _, rules in sheets for r in rules]
    while level:
        units = [r.rid for r in level]
        found = _ddmin_units(html_text, units, lambda kept, u=units: render(u, kept), test)
        if found is None:
            break
        html_text, kept = found
        removed |= set(units) - set(kept)
        level = [c for r in level if r.rid not in removed and r.children for c in r.children]
    return html_text


def reduce_declarations(html_text: str, test: Test) -> str:
    """Removes declarations from ``<style>`` rules and ``style=""`` attributes."""
    doc = Doc(html_text)
    sheets = _style_sheets(doc)
    units: list[tuple] = [
        ("css", r.rid, k) for _, rules in sheets for r in iter_rules(rules) if r.decls for k in range(len(r.decls))
    ]
    styled = []  # (node id, head, attrs, tail, index of style attr, declarations)
    for n in doc.elements():
        head, attrs, tail = split_start_tag(n.raw)
        for idx, a in enumerate(attrs):
            if attr_name(a) == "style":
                decls = split_declarations(attr_value(a) or "")
                styled.append((n.id, head, attrs, tail, idx, decls))
                units.extend(("attr", n.id, k) for k in range(len(decls)))
                break

    def render(kept: list) -> str:
        keep = set(kept)
        removed_decls = {(rid, k) for kind, rid, k in units if kind == "css" and (kind, rid, k) not in keep}
        inner = {sid: "\n" + serialize_css(rules, set(), removed_decls) + "\n" for sid, rules in sheets}
        start = {}
        for nid, head, attrs, tail, idx, decls in styled:
            left = [d for k, d in enumerate(decls) if ("attr", nid, k) in keep]
            new = list(attrs)
            if left:
                new[idx] = make_attr("style", "; ".join(left))
            else:
                del new[idx]
            start[nid] = join_start_tag(head, new, tail)
        return doc.render(inner=inner, start=start)

    return _reduce_units(html_text, units, render, test)


def reduce_attributes(html_text: str, test: Test) -> str:
    """Removes attributes from elements."""
    doc = Doc(html_text)
    tags = {}
    units = []
    for n in doc.elements():
        head, attrs, tail = split_start_tag(n.raw)
        if attrs:
            tags[n.id] = (head, attrs, tail)
            units.extend((n.id, k) for k in range(len(attrs)))

    def render(kept: list) -> str:
        keep = set(kept)
        start = {
            nid: join_start_tag(head, [a for k, a in enumerate(attrs) if (nid, k) in keep], tail)
            for nid, (head, attrs, tail) in tags.items()
            if any((nid, k) not in keep for k in range(len(attrs)))
        }
        return doc.render(start=start)

    return _reduce_units(html_text, units, render, test)


SKIP_TEXT = RAW_TEXT - {"title"}
KEEP_WHITESPACE = {"pre", "textarea", "script", "style", "listing", "plaintext", "xmp"}


def _ancestors(node: Node) -> Iterator[Node]:
    while node.parent is not None:
        node = node.parent
        yield node


def reduce_text(html_text: str, test: Test) -> str:
    """Removes whitespace between elements, then shortens text nodes (longest first): collapse whitespace, keep
    halves of the words, then of the characters, as long as the finding stays."""
    doc = Doc(html_text)
    nodes = [
        n
        for n in doc.nodes
        if n.kind == "text" and len(n.raw.strip()) > 1 and (n.parent is None or n.parent.tag not in SKIP_TEXT)
    ]
    nodes.sort(key=lambda n: -len(n.raw))
    accepted: dict[int, str] = {}
    try:
        # whitespace between elements: all of it removed at once, else collapsed to one space
        blanks = [
            n.id
            for n in doc.nodes
            if n.kind == "text" and n.raw and not n.raw.strip() and n.raw != " "
            if not any(a.tag in KEEP_WHITESPACE for a in _ancestors(n))
        ]
        for filler in ("", " "):
            if blanks and test(doc.render(replace=dict.fromkeys(blanks, filler))):
                accepted.update(dict.fromkeys(blanks, filler))
                break
        for node in nodes:

            def attempt(new: str, nid: int = node.id) -> bool:
                candidate = doc.render(replace={**accepted, nid: new})
                if test(candidate):
                    accepted[nid] = new
                    return True
                return False

            current = node.raw
            collapsed = re.sub(r"\s+", " ", current)
            if collapsed != current and attempt(collapsed):
                current = collapsed
            words = re.findall(r"\s*\S+\s*", current)
            while len(words) > 1:
                half = len(words) // 2
                first, second = "".join(words[:half]), "".join(words[half:])
                if attempt(first):
                    words = words[:half]
                elif attempt(second):
                    words = words[half:]
                else:
                    break
            current = "".join(words)
            while len(current.strip()) > 1:
                core = current.strip()
                half = core[: len(core) // 2]
                if not attempt(half):
                    break
                current = half
    except BudgetExhausted:
        pass
    return doc.render(replace=accepted)


# --- WARC reading and offline resources --------------------------------------------------------------------------


@dataclass
class WarcRecord:
    headers: dict[str, str]  # lower-case names
    content: bytes

    @property
    def type(self) -> str:
        return self.headers.get("warc-type", "")

    @property
    def target(self) -> str:
        return self.headers.get("warc-target-uri", "")


def iter_warc(source: str | Path | io.BufferedIOBase) -> Iterator[WarcRecord]:
    """The records of a WARC file (gzip members, one per record, or plain)."""
    if isinstance(source, (str, Path)):
        raw = open(source, "rb")
    else:
        raw = source
    try:
        if hasattr(raw, "peek"):
            magic = raw.peek(2)[:2]
        else:
            magic = raw.read(2)
            raw.seek(-len(magic), 1)
        stream = gzip.GzipFile(fileobj=raw) if magic == b"\x1f\x8b" else raw
        while True:
            line = stream.readline()
            if not line:
                return
            if not line.strip():
                continue
            if not line.startswith(b"WARC/"):
                raise ValueError(f"not a WARC record: {line[:40]!r}")
            headers: dict[str, str] = {}
            while True:
                line = stream.readline()
                if not line or not line.strip():
                    break
                name, _, value = line.decode("utf-8", "replace").partition(":")
                headers[name.strip().lower()] = value.strip()
            length = int(headers.get("content-length", "0"))
            content = stream.read(length)
            yield WarcRecord(headers, content)
    finally:
        if isinstance(source, (str, Path)):
            raw.close()


class UnsupportedEncoding(ValueError):
    pass


def _dechunk(body: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(body):
        j = body.find(b"\r\n", i)
        if j < 0:
            break
        size = int(body[i:j].split(b";")[0] or b"0", 16)
        if size == 0:
            break
        out += body[j + 2 : j + 2 + size]
        i = j + 2 + size + 2
    return bytes(out)


def parse_http_response(block: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    """(status, headers, decoded body) of an HTTP response block; gzip and deflate are decoded, br and zstd when the
    brotli and zstandard modules are installed (the worker image has them); other encodings raise
    ``UnsupportedEncoding``."""
    sep = block.find(b"\r\n\r\n")
    head, body = (block[:sep], block[sep + 4 :]) if sep >= 0 else (block, b"")
    lines = head.decode("iso-8859-1").split("\r\n")
    status = int(lines[0].split()[1]) if len(lines[0].split()) > 1 else 0
    headers = []
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers.append((name.strip(), value.strip()))
    lower = {k.lower(): v.lower() for k, v in headers}
    if "chunked" in lower.get("transfer-encoding", ""):
        body = _dechunk(body)
    for coding in reversed([c.strip() for c in lower.get("content-encoding", "").split(",") if c.strip()]):
        if coding in ("gzip", "x-gzip"):
            body = gzip.decompress(body)
        elif coding == "deflate":
            try:
                body = zlib.decompress(body)
            except zlib.error:
                body = zlib.decompress(body, -zlib.MAX_WBITS)
        elif coding in _DECODERS and _DECODERS[coding]() is not None:
            try:
                body = _DECODERS[coding]()(body)
            except Exception as exc:  # brotli.error and zstandard.ZstdError are not ValueErrors
                raise ValueError(f"bad {coding} body") from exc
        elif coding != "identity":
            raise UnsupportedEncoding(coding)
    return status, headers, body


def _brotli():
    try:
        import brotli
    except ImportError:
        return None
    return brotli.decompress


def _zstd():
    try:
        from compression import zstd  # Python 3.14
    except ImportError:
        try:
            import zstandard
        except ImportError:
            return None
        return lambda data: zstandard.ZstdDecompressor().decompressobj().decompress(data)
    return zstd.decompress


_DECODERS = {"br": _brotli, "zstd": _zstd}


@dataclass
class Response:
    url: str
    status: int
    headers: list[tuple[str, str]]
    body: bytes
    user_agent: str = ""

    def header(self, name: str) -> str:
        return next((v for k, v in self.headers if k.lower() == name), "")


def load_responses(warc: str | Path, notes: list[str] | None = None) -> dict[str, list[Response]]:
    """GET responses in a recording by URL, each with the user agent of its request."""
    agents: dict[str, str] = {}
    pending = []
    for record in iter_warc(warc):
        if record.type == "request":
            ua = re.search(rb"(?im)^user-agent:\s*(.*?)\r?$", record.content)
            agents[record.headers.get("warc-record-id", "")] = ua.group(1).decode("utf-8", "replace") if ua else ""
        elif record.type == "response":
            if record.headers.get("warc-webparity-method", "GET").upper() != "GET":
                continue
            pending.append(record)
    out: dict[str, list[Response]] = {}
    for record in pending:
        try:
            status, headers, body = parse_http_response(record.content)
        except (UnsupportedEncoding, OSError, zlib.error, ValueError) as exc:
            if notes is not None:
                notes.append(f"skipped {record.target}: {type(exc).__name__} {exc}")
            continue
        ua = agents.get(record.headers.get("warc-concurrent-to", ""), "")
        out.setdefault(record.target, []).append(Response(record.target, status, headers, body, ua))
    return out


def local_path_for(url: str) -> str:
    """A relative path (``res/host/path``) for a URL's local copy; a query becomes a hash in the name."""
    parts = urllib.parse.urlsplit(url)
    host = re.sub(r"[^A-Za-z0-9.-]", "_", parts.hostname or "host") + (f"_{parts.port}" if parts.port else "")
    segments = [re.sub(r"[^A-Za-z0-9._-]", "_", urllib.parse.unquote(s))[:100] for s in parts.path.split("/") if s]
    segments = [s if s not in (".", "..") else "_" for s in segments]
    if not segments or parts.path.endswith("/"):
        segments.append("index.html")
    if parts.query:
        stem, dot, ext = segments[-1].rpartition(".")
        digest = hashlib.sha1(parts.query.encode()).hexdigest()[:10]
        segments[-1] = f"{stem}_{digest}.{ext}" if dot and stem else f"{segments[-1]}_{digest}"
    return posixpath.join("res", host, *segments)


def _pick(responses: list[Response], prefer: str | None) -> Response:
    """The response recorded for the preferred browser's user agent (Ladybird's says "Ladybird"), else the first."""
    lady = [r for r in responses if "ladybird" in r.user_agent.lower()]
    other = [r for r in responses if "ladybird" not in r.user_agent.lower()]
    return ((lady if prefer == "ladybird" else other) or responses)[0]


def materialize(responses: dict[str, list[Response]], dest: Path, prefer: str | None = None) -> dict[str, str]:
    """Writes the recorded responses under ``dest`` and returns URL → relative path; redirects map to their target."""
    chosen = {url: _pick(rs, prefer) for url, rs in responses.items()}
    mapping: dict[str, str] = {}
    for url, r in chosen.items():
        if 300 <= r.status < 400:
            continue
        rel = local_path_for(url)
        try:
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            if (dest / rel).is_dir():
                rel = posixpath.join(rel, "index.html")
            (dest / rel).write_bytes(r.body)
        except (FileExistsError, NotADirectoryError, IsADirectoryError):
            # /a and /a/b.css both recorded: the second one goes to a flat name
            ext = posixpath.splitext(rel)[1][:10]
            rel = posixpath.join("res", "_", hashlib.sha1(url.encode()).hexdigest()[:16] + ext)
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            (dest / rel).write_bytes(r.body)
        mapping[url] = rel
    for url in chosen:
        target, seen = url, set()
        while target in chosen and 300 <= chosen[target].status < 400 and target not in seen:
            seen.add(target)
            target = urllib.parse.urljoin(target, chosen[target].header("location"))
        if target in mapping and url not in mapping:
            mapping[url] = mapping[target]
    return mapping


URL_ATTRS = {"src", "href", "poster", "data", "background", "action", "formaction", "icon", "manifest"}
SRCSET_ATTRS = {"srcset", "imagesrcset"}


def _lookup(ref: str, base: str, mapping: dict[str, str]) -> str | None:
    ref = ref.strip()
    if not ref or ref.startswith(("data:", "javascript:", "#", "blob:", "about:")):
        return None
    absolute = urllib.parse.urljoin(base, ref)
    url, _, fragment = absolute.partition("#")
    rel = mapping.get(url)
    if rel is None:
        return None
    return rel + (f"#{fragment}" if fragment else "")


def _rewrite_css_urls(css: str, base: str, mapping: dict[str, str], from_dir: str) -> str:
    def sub(m: re.Match) -> str:
        ref = m.group(2) if m.group(2) is not None else m.group(4)
        rel = _lookup(ref, base, mapping)
        if rel is None:
            return m.group(0)
        rel = posixpath.relpath(rel, from_dir or ".")
        return f'url("{rel}")' if m.group(2) is not None else f'@import "{rel}"'

    return _CSS_URL_RE.sub(sub, css)


def rewrite_html_urls(html_text: str, base: str, mapping: dict[str, str], from_dir: str = "") -> str:
    """Points the document's references to recorded resources at their local copies (relative to ``from_dir``), drops
    ``<base>`` and ``integrity``, and declares UTF-8."""
    doc = Doc(html_text)
    for n in doc.elements("base"):
        href = attributes(n).get("href")
        if href:
            base = urllib.parse.urljoin(base, href)
            break

    def local(ref: str) -> str | None:
        rel = _lookup(ref, base, mapping)
        return None if rel is None else posixpath.relpath(rel, from_dir or ".")

    removed, start, inner = set(), {}, {}
    for n in doc.elements():
        if n.tag == "base":
            removed.add(n.id)
            continue
        head, attrs, tail = split_start_tag(n.raw)
        new, changed = [], False
        for a in attrs:
            name, value = attr_name(a), attr_value(a)
            if value is not None and name in URL_ATTRS and (rel := local(value)) is not None:
                new.append(make_attr(name, rel))
                changed = True
            elif value is not None and name in SRCSET_ATTRS:
                items = []
                for item in value.split(","):
                    ref, _, descriptor = item.strip().partition(" ")
                    rel = local(ref)
                    items.append(" ".join(x for x in (rel or ref, descriptor.strip()) if x))
                new.append(make_attr(name, ", ".join(items)))
                changed = True
            elif value is not None and name == "style" and "url(" in value:
                new.append(make_attr(name, _rewrite_css_urls(value, base, mapping, from_dir)))
                changed = True
            elif n.tag == "meta" and name == "charset":
                new.append('charset="utf-8"')
                changed = True
            elif n.tag == "meta" and name == "content" and "charset=" in (value or "").lower():
                new.append(make_attr(name, "text/html; charset=utf-8"))
                changed = True
            else:
                new.append(a)
        if changed:
            new = [a for a in new if attr_name(a) != "integrity"]
            start[n.id] = join_start_tag(head, new, tail)
        if n.tag == "style":
            inner[n.id] = _rewrite_css_urls(doc.text_content(n), base, mapping, from_dir)
    return doc.render(removed=removed, start=start, inner=inner)


# A leading doctype, after any comments and whitespace (older bundles saved the DOM without one)
DOCTYPE = re.compile(r"\A(?:\s|<!--.*?-->)*(<!doctype[^>]*>)", re.I | re.S)


@dataclass
class Prepared:
    html: str
    serve_dir: Path
    base_url: str | None = None
    notes: list[str] = field(default_factory=list)


def is_bundle(source: str | Path) -> bool:
    p = Path(source)
    return (p.is_dir() and (p / "visit.json").exists()) or p.name == "visit.json"


def prepare_source(source: str | Path, work_dir: Path, dom: str = "ladybird", resources: bool = True) -> Prepared:
    """The starting document and the directory candidates are served from.

    A local HTML file is served from its own directory. A visit bundle's saved DOM is written to ``work_dir/site``,
    with the resources from its recording (when ``resources``) under ``site/res`` and the page's URLs rewritten."""
    source = Path(source)
    if not is_bundle(source):
        text = source.read_bytes().decode("utf-8", "surrogateescape")
        return Prepared(text, source.resolve().parent)
    from webparity.bundle import Bundle

    bundle = Bundle.load(source)
    notes: list[str] = []
    order = [dom] + [b for b in ("ladybird", "chromium") if b != dom]
    content, used = None, None
    for name in order:
        content = bundle.read_file(f"{name}-dom.html.gz")
        if content is not None:
            used = name
            break
    if content is None:
        raise ReduceError(f"{bundle.path}: no saved DOM (ladybird-dom.html.gz or chromium-dom.html.gz)")
    if used != dom:
        notes.append(f"no {dom} DOM in the bundle; using the {used} DOM")
    text = content.decode("utf-8", "surrogateescape")
    base = bundle.m(used, "load", "url") or bundle.data.get("url") or ""
    site = work_dir / "site"
    site.mkdir(parents=True, exist_ok=True)
    warc = bundle.path / "recording.warc.gz"
    if resources and warc.exists():
        responses = load_responses(warc, notes)
        skipped = sum(1 for n in notes if n.startswith("skipped "))
        if skipped:
            notes.append(
                f"warning: {skipped} recorded responses could not be decoded and are missing offline; "
                "the reduction may chase a different difference than the page has"
            )
        if not DOCTYPE.match(text) and base in responses:
            page = _pick(responses[base], used).body.decode("utf-8", "replace")
            m = DOCTYPE.match(page)
            if m:
                text = m.group(1) + "\n" + text
                notes.append(f"the saved DOM has no doctype; using the recorded page's {m.group(1)}")
        mapping = materialize(responses, site, prefer=used)
        for url, rel in mapping.items():
            path = site / rel
            ctype = _pick(responses[url], used).header("content-type").lower() if url in responses else ""
            if "css" in ctype or rel.endswith(".css"):
                css = path.read_bytes().decode("utf-8", "surrogateescape")
                path.write_bytes(_enc(_rewrite_css_urls(css, url, mapping, posixpath.dirname(rel))))
            elif "html" in ctype and url != base:
                doc_text = path.read_bytes().decode("utf-8", "surrogateescape")
                path.write_bytes(_enc(rewrite_html_urls(doc_text, url, mapping, posixpath.dirname(rel))))
        text = rewrite_html_urls(text, base, mapping)
        notes.append(f"{len(mapping)} recorded resources written to {site / 'res'}")
    elif resources:
        notes.append("the bundle has no recording; the page's resources are not available offline")
    (site / "original.html").write_bytes(_enc(text))
    return Prepared(text, site, base, notes)


def local_references(html_text: str, root: Path) -> list[str]:
    """Local files a document (at ``root``) still refers to."""
    refs = set()
    doc = Doc(html_text)
    for n in doc.elements():
        for name, value in attributes(n).items():
            if value and name in URL_ATTRS | SRCSET_ATTRS:
                for item in value.split(",") if name in SRCSET_ATTRS else [value]:
                    ref = item.strip().split(" ")[0]
                    if _local_file(ref, root):
                        refs.add(ref)
    for m in _CSS_URL_RE.finditer(html_text):
        ref = m.group(2) if m.group(2) is not None else m.group(4)
        if ref and _local_file(ref, root):
            refs.add(ref)
    return sorted(refs)


# --- predicates --------------------------------------------------------------------------------------------------

CRASH_RE = r"crash|connection closed|websocket closed|connection reset|broken pipe|connection refused"
Condition = Callable[[dict[str, Any]], bool]


def _ok(result: Any) -> bool:
    return result is not None and not result.error


def differs(results: dict[str, Any]) -> bool:
    """Ladybird and Chromium both answered, with different values."""
    lb, cr = results.get("ladybird"), results.get("chromium")
    return _ok(lb) and _ok(cr) and lb.value != cr.value


def ladybird_equals(value: str) -> Condition:
    return lambda results: _ok(results.get("ladybird")) and (results["ladybird"].value or "").strip() == value.strip()


def ladybird_matches(pattern: str) -> Condition:
    rx = re.compile(pattern)
    return lambda results: _ok(results.get("ladybird")) and rx.search(results["ladybird"].value or "") is not None


def ladybird_error(pattern: str = CRASH_RE) -> Condition:
    """Ladybird's probe failed with an error matching ``pattern`` while every other browser probed was fine."""
    rx = re.compile(pattern, re.I)

    def condition(results: dict[str, Any]) -> bool:
        lb = results.get("ladybird")
        if lb is None or not lb.error or not rx.search(lb.error):
            return False
        return all(_ok(r) for name, r in results.items() if name != "ladybird")

    return condition


class ProbePredicate:
    """Runs a candidate file through the probe and applies ``condition`` to the results (browser name → Result).

    Sessions are kept between candidates (a session that fails is replaced); ``fresh`` starts new sessions for every
    candidate through ``webparity.probe.probe``. With ``repeat`` > 1 every run must hold (or any run, with
    ``repeat_any``, for findings that only show sometimes)."""

    def __init__(
        self,
        condition: Condition,
        script: str | None = None,
        browsers: Sequence[str] = ("ladybird", "chromium"),
        wait: float | None = None,
        mobile: bool = False,
        fresh: bool = False,
        repeat: int = 1,
        repeat_any: bool = False,
    ):
        self.condition, self.script, self.browsers = condition, script, list(browsers)
        self.wait, self.mobile, self.fresh = wait, mobile, fresh
        self.repeat, self.repeat_any = max(1, repeat), repeat_any
        self.last: dict[str, Any] = {}
        self._server = None
        self._open: dict[str, Any] = {}

    def __call__(self, path: Path) -> bool:
        for _ in range(self.repeat):
            self.last = self.run(Path(path))
            ok = bool(self.condition(self.last))
            if ok == self.repeat_any:
                return ok
        return not self.repeat_any

    def run(self, path: Path) -> dict[str, Any]:
        from webparity import probe

        if self.fresh:
            results = probe.probe(
                [str(path)], self.script, self.browsers, self.wait, self.mobile, fresh=True, root=str(path.parent)
            )
            return {r.browser: r for r in results}
        if self._server is None or self._server.root != path.parent.resolve():
            self.close()
            self._server = probe.Server(path.parent)
        url = self._server.url_for(path)
        out = {}
        for name in self.browsers:
            browser = self._open.setdefault(name, probe.Browser(name, self.mobile))
            started = time.monotonic()
            result = probe.Result(url, name)
            try:
                if browser.session is None:
                    browser.start()
                browser.load(url, True, self.wait)
                result.value = browser.run_script(self.script, True)
            except (probe.BidiError, OSError, KeyError) as exc:
                result.error = str(exc)[:300]
                browser.stop()
            result.ms = int((time.monotonic() - started) * 1000)
            out[name] = result
        return out

    def close(self) -> None:
        for browser in self._open.values():
            browser.stop()
        self._open = {}
        if self._server is not None:
            self._server.close()
            self._server = None


# --- the driver --------------------------------------------------------------------------------------------------


class ReduceError(RuntimeError):
    pass


@dataclass
class ReduceResult:
    out_path: Path
    work_dir: Path
    bytes_before: int
    bytes_after: int
    tests: int = 0  # probes run
    cached: int = 0  # candidates answered from the cache
    passes: list[dict] = field(default_factory=list)  # {"name", "round", "bytes_before", "bytes_after", "tests"}
    interrupted: str | None = None  # "budget" or "interrupt"
    resources: list[str] = field(default_factory=list)  # local files the result still refers to
    best_path: Path | None = None  # the best document so far, kept during the run
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0


class _Tester:
    """Writes candidates to fresh files, calls the predicate, caches answers by content and keeps the best."""

    def __init__(self, check, serve_dir: Path, work_dir: Path, initial: str, budget, keep, progress):
        self.check, self.serve_dir, self.work_dir = check, serve_dir, work_dir
        self.budget, self.keep, self.progress = budget, keep, progress
        self.cache: dict[str, bool] = {}
        self.tests = self.cached = 0
        self.best, self.best_bytes = initial, _size(initial)
        self.exhausted = False
        self.pass_name = ""
        # next to the resources when they live in the work directory (a bundle), so best.html runs as it is
        inside = serve_dir.resolve() == work_dir.resolve() or work_dir.resolve() in serve_dir.resolve().parents
        self.best_path = (serve_dir if inside else work_dir) / "best.html"

    def set_best(self, text: str) -> None:
        self.best, self.best_bytes = text, _size(text)
        _write_atomic(self.best_path, _enc(text))

    def __call__(self, text: str) -> bool:
        data = _enc(text)
        key = hashlib.sha1(data).hexdigest()
        if key in self.cache:
            self.cached += 1
            return self.cache[key]
        if self.budget is not None and self.tests >= self.budget:
            self.exhausted = True
            raise BudgetExhausted
        self.tests += 1
        path = self.serve_dir / f"{CANDIDATE_PREFIX}{os.getpid()}-{self.tests:06d}.html"
        path.write_bytes(data)
        try:
            ok = bool(self.check(path))
        finally:
            if not self.keep:
                path.unlink(missing_ok=True)
        self.cache[key] = ok
        improved = ok and len(data) < self.best_bytes
        if improved:
            self.set_best(text)
        if self.progress:
            self.progress(
                {
                    "event": "improved" if improved else "test",
                    "pass": self.pass_name,
                    "tests": self.tests,
                    "interesting": ok,
                    "bytes": len(data),
                    "best": self.best_bytes,
                }
            )
        return ok


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def reduce(
    source: str | Path,
    predicate: Callable[[Path], bool],
    out_path: str | Path,
    budget_tests: int | None = 1000,
    browsers: Sequence[str] | None = None,
    work_dir: str | Path | None = None,
    passes: Sequence[str] = PASSES,
    rounds: int = 3,
    dom: str = "ladybird",
    resources: bool = True,
    protect: Sequence[str] = (),
    keep_candidates: bool = False,
    progress: Callable[[dict], None] | None = None,
) -> ReduceResult:
    """Reduces ``source`` (an HTML file or a visit bundle) while ``predicate(candidate_path)`` holds.

    ``predicate`` is a ``ProbePredicate`` or any callable taking the candidate's path. ``browsers`` overrides a
    ``ProbePredicate``'s browsers. The best document so far is kept in ``best.html`` (in the work directory, or in
    ``work_dir/site`` next to a bundle's resources) after every improvement;
    the result is written to ``out_path`` at the end, also when the budget runs out or the run is interrupted."""
    started = time.monotonic()
    out_path = Path(out_path)
    work = Path(work_dir) if work_dir else out_path.with_name(out_path.name + ".work")
    work.mkdir(parents=True, exist_ok=True)
    unknown = [p for p in passes if p not in PASSES]
    if unknown:
        raise ReduceError(f"unknown passes: {', '.join(unknown)} (known: {', '.join(PASSES)})")
    if browsers is not None and isinstance(predicate, ProbePredicate):
        predicate.browsers = list(browsers)
    prepared = prepare_source(source, work, dom=dom, resources=resources)
    text = prepared.html
    result = ReduceResult(out_path, work, _size(text), _size(text), notes=list(prepared.notes))
    tester = _Tester(predicate, prepared.serve_dir, work, text, budget_tests, keep_candidates, progress)
    tester.set_best(text)
    try:
        tester.pass_name = "start"
        if not tester(text):
            raise ReduceError("the starting document does not show the finding (the predicate is false)")
        for round_no in range(1, rounds + 1):
            size_at_start = tester.best_bytes
            for name in (p for p in PASSES if p in passes):
                tester.pass_name = name
                current = tester.best
                before, tests_before = tester.best_bytes, tester.tests
                if progress:
                    progress({"event": "pass", "pass": name, "round": round_no, "best": before})
                if name == "scripts":
                    reduced = reduce_scripts(current, tester, prepared.serve_dir, protect)
                elif name == "stylesheets":
                    reduced = reduce_stylesheets(current, tester, prepared.serve_dir)
                elif name == "dom":
                    reduced = reduce_dom(current, tester, protect)
                elif name == "css-rules":
                    reduced = reduce_css_rules(current, tester)
                elif name == "declarations":
                    reduced = reduce_declarations(current, tester)
                elif name == "attributes":
                    reduced = reduce_attributes(current, tester)
                else:
                    reduced = reduce_text(current, tester)
                # Every reducer returns an interesting document; inlining can make it bigger than the input, and it
                # is still the one to continue from.
                tester.set_best(reduced)
                result.passes.append(
                    {
                        "name": name,
                        "round": round_no,
                        "bytes_before": before,
                        "bytes_after": tester.best_bytes,
                        "tests": tester.tests - tests_before,
                    }
                )
                if tester.exhausted:
                    raise BudgetExhausted
            if tester.best_bytes >= size_at_start and round_no > 1:
                break
    except BudgetExhausted:
        result.interrupted = "budget"
    except KeyboardInterrupt:
        result.interrupted = "interrupt"
    finally:
        close = getattr(predicate, "close", None)
        if close:
            close()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(out_path, _enc(tester.best))
    result.bytes_after = tester.best_bytes
    result.tests, result.cached = tester.tests, tester.cached
    result.resources = local_references(tester.best, prepared.serve_dir)
    result.seconds = time.monotonic() - started
    result.best_path = tester.best_path
    return result


# --- command line ------------------------------------------------------------------------------------------------


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("source", help="an HTML file, or a visit bundle (its directory or visit.json)")
    p.add_argument("-o", "--out", required=True, help="where to write the reduced test case")
    script = p.add_mutually_exclusive_group()
    script.add_argument("-j", "--js", help="an expression, or statements with their own return (default: #out/title)")
    script.add_argument("--js-file", help="read the script from a file")
    cond = p.add_mutually_exclusive_group(required=True)
    cond.add_argument("--differs", action="store_true", help="Ladybird's result differs from Chromium's")
    cond.add_argument("--ladybird-equals", metavar="VALUE", help="Ladybird's result equals VALUE")
    cond.add_argument("--ladybird-matches", metavar="REGEX", help="Ladybird's result matches REGEX")
    cond.add_argument("--crash", action="store_true", help="Ladybird crashes (connection lost) while Chromium is fine")
    cond.add_argument("--ladybird-error", metavar="REGEX", help="Ladybird's probe error matches REGEX, others fine")
    p.add_argument("--browsers", help="comma-separated (default: ladybird,chromium; ladybird for -equals/-matches)")
    p.add_argument("--wait", type=float, help="seconds after loading each candidate (default 0.3)")
    p.add_argument("--mobile", action="store_true", help="a 390x844 viewport instead of 1440x900")
    p.add_argument("--fresh", action="store_true", help="new browser sessions for every candidate")
    p.add_argument("--repeat", type=int, default=1, help="probe each candidate N times; all must hold (default 1)")
    p.add_argument("--repeat-any", action="store_true", help="with --repeat: one run holding is enough (flaky finds)")
    p.add_argument("--budget", type=int, default=1000, help="the most candidates to probe (default 1000)")
    p.add_argument("--rounds", type=int, default=3, help="repeat all passes while they help, at most N (default 3)")
    p.add_argument("--passes", default=",".join(PASSES), help=f"comma-separated subset of: {','.join(PASSES)}")
    p.add_argument("--work-dir", help="best-so-far and resources (default: OUT.work)")
    p.add_argument("--dom", choices=("ladybird", "chromium"), default="ladybird", help="bundle: whose saved DOM")
    p.add_argument("--no-resources", action="store_true", help="bundle: don't write the recorded resources")
    p.add_argument("--protect", action="append", default=[], help="never remove nodes containing this text")
    p.add_argument("--keep-candidates", action="store_true", help="don't delete candidate files after probing")
    p.add_argument("-q", "--quiet", action="store_true", help="only print the summary")


def predicate_from_args(args: argparse.Namespace) -> ProbePredicate:
    script = open(args.js_file, encoding="utf-8").read() if args.js_file else args.js
    if args.differs:
        condition, default = differs, "ladybird,chromium"
    elif args.ladybird_equals is not None:
        condition, default = ladybird_equals(args.ladybird_equals), "ladybird"
    elif args.ladybird_matches is not None:
        condition, default = ladybird_matches(args.ladybird_matches), "ladybird"
    elif args.crash:
        condition, default = ladybird_error(CRASH_RE), "ladybird,chromium"
    else:
        condition, default = ladybird_error(args.ladybird_error), "ladybird,chromium"
    browsers = [b.strip() for b in (args.browsers or default).split(",") if b.strip()]
    if args.differs and not {"ladybird", "chromium"} <= set(browsers):
        raise ReduceError("--differs needs both ladybird and chromium in --browsers")
    return ProbePredicate(condition, script, browsers, args.wait, args.mobile, args.fresh, args.repeat, args.repeat_any)


def run(args: argparse.Namespace) -> int:
    def progress(event: dict) -> None:
        if args.quiet:
            return
        if event["event"] == "pass":
            print(f"[{event['pass']}] round {event['round']}, {event['best']} bytes", file=sys.stderr, flush=True)
        elif event["event"] == "improved":
            print(f"  test {event['tests']}: {event['best']} bytes", file=sys.stderr, flush=True)

    try:
        predicate = predicate_from_args(args)
        result = reduce(
            args.source,
            predicate,
            args.out,
            budget_tests=args.budget,
            work_dir=args.work_dir,
            passes=[p.strip() for p in args.passes.split(",") if p.strip()],
            rounds=args.rounds,
            dom=args.dom,
            resources=not args.no_resources,
            protect=args.protect,
            keep_candidates=args.keep_candidates,
            progress=progress,
        )
    except (ReduceError, OSError, ValueError) as exc:
        print(f"webparity reduce: {exc}", file=sys.stderr)
        return 2
    for note in result.notes:
        print(f"note: {note}")
    status = f" (stopped: {result.interrupted})" if result.interrupted else ""
    print(
        f"{result.bytes_before} -> {result.bytes_after} bytes in {result.tests} probes"
        f" ({result.cached} cached, {result.seconds:.0f} s){status}: {result.out_path}"
    )
    if result.resources:
        print(f"the result still refers to local files (next to {result.best_path}):")
        for ref in result.resources:
            print(f"  {ref}")
    return 1 if result.interrupted == "interrupt" else 0


def main_reduce(args_list: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="webparity reduce", description="Reduce a finding to a small test case.")
    add_arguments(parser)
    return run(parser.parse_args(args_list))


if __name__ == "__main__":
    sys.exit(main_reduce())
