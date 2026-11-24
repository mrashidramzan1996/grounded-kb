import ast
import hashlib
from pathlib import PurePosixPath

import tree_sitter_python
from tree_sitter import Language, Parser, Query, QueryCursor

from kb.ports.code import Symbol

_LANGUAGE = Language(tree_sitter_python.language())

# Only module-level functions/classes and methods of module-level classes are anchors.
_FUNCTIONS = """
(module [
  (function_definition name: (identifier) @name) @def
  (decorated_definition definition: (function_definition name: (identifier) @name)) @def
])
"""
_CLASSES = """
(module [
  (class_definition name: (identifier) @name) @def
  (decorated_definition definition: (class_definition name: (identifier) @name)) @def
])
"""
_METHODS = """
(module [
  (class_definition name: (identifier) @cls
    body: (block [
      (function_definition name: (identifier) @name) @def
      (decorated_definition definition: (function_definition name: (identifier) @name)) @def
    ]))
  (decorated_definition definition: (class_definition name: (identifier) @cls
    body: (block [
      (function_definition name: (identifier) @name) @def
      (decorated_definition definition: (function_definition name: (identifier) @name)) @def
    ])))
])
"""
_QUERIES = {kind: Query(_LANGUAGE, text) for kind, text in (("function", _FUNCTIONS), ("class", _CLASSES), ("method", _METHODS))}


def _is_test_file(path: str) -> bool:
    p = PurePosixPath(path)
    return p.name.startswith("test_") or p.stem.endswith("_test") or "tests" in p.parts[:-1]


def _text(node) -> str:
    return node.text.decode("utf-8")


class PythonPack:
    name = "python"

    def handles(self, path: str) -> bool:
        return path.endswith(".py")

    def has_syntax_errors(self, source: bytes) -> bool:
        return Parser(_LANGUAGE).parse(source).root_node.has_error

    def symbols(self, path: str, source: bytes) -> list[Symbol]:
        # on syntax errors tree-sitter still recovers most definitions; they are returned as found
        tree = Parser(_LANGUAGE).parse(source)
        test_file = _is_test_file(path)
        out: list[Symbol] = []
        for kind, query in _QUERIES.items():
            for _, caps in QueryCursor(query).matches(tree.root_node):
                node, name = caps["def"][0], _text(caps["name"][0])
                if kind == "method":
                    name = f"{_text(caps['cls'][0])}.{name}"
                sym_kind = "test" if kind != "class" and test_file and name.rsplit(".", 1)[-1].startswith("test") else kind
                out.append(Symbol(f"{path}#{name}", sym_kind, path, name, node.start_byte, node.end_byte))
        return sorted(out, key=lambda s: (s.start_byte, s.anchor))

    def fingerprint(self, source: bytes, symbol: Symbol) -> str:
        """16 hex chars over the symbol's normalised token stream (formatting, comments, docstrings ignored)."""
        root = Parser(_LANGUAGE).parse(source).root_node
        node = root.descendant_for_byte_range(symbol.start_byte, symbol.end_byte)
        while node is not None and node.parent is not None and node.parent.start_byte == node.start_byte and node.parent.end_byte == node.end_byte:
            node = node.parent
        tokens: list[str] = []
        if node is not None:
            _tokens(node, tokens)
        return hashlib.sha256("\n".join(tokens).encode("utf-8")).hexdigest()[:16]


_TEXT_LEAVES = frozenset({"identifier", "integer", "float"})
_CLOSERS = frozenset({")", "]", "}"})
_KEEP_PARENS = frozenset({"call", "argument_list", "generator_expression"})


def _is_docstring(node) -> bool:
    parent = node.parent
    if node.type != "expression_statement" or node.child_count != 1 or node.children[0].type != "string":
        return False
    if parent is None or parent.type != "block" or parent.named_children[0].id != node.id:
        return False
    return parent.parent is not None and parent.parent.type in ("function_definition", "class_definition")


def _string_token(node) -> str:
    text = _text(node)
    try:
        return "str:" + repr(ast.literal_eval(text))
    except Exception:  # f-strings and anything literal_eval rejects: raw text, delimiter quotes normalised
        i = 0
        while i < len(text) and text[i] not in "'\"":
            i += 1
        prefix, rest = text[:i], text[i:]
        n = 3 if rest[:3] in ("'''", '"""') else 1
        return f"fstr:{prefix}" + '"' * n + rest[n : len(rest) - n] + '"' * n


def _tokens(node, out: list[str]) -> None:
    kind = node.type
    if kind == "comment" or _is_docstring(node):
        return
    if kind == "string":
        out.append(_string_token(node))
        return
    if node.child_count == 0:
        if kind == ",":
            nxt = node.next_sibling
            if nxt is not None and nxt.type in _CLOSERS:
                return
        elif kind in ("(", ")"):
            parent = node.parent
            if parent is not None and parent.type == "parenthesized_expression":
                gp = parent.parent
                if gp is None or gp.type not in _KEEP_PARENS:
                    return
        out.append(f"{kind}:{_text(node)}" if kind in _TEXT_LEAVES or not node.is_named else kind)
        return
    for child in node.children:
        _tokens(child, out)
