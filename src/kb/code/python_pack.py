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
