"""Minimal KDL (v1) reader and writer for JFShell's configuration files.

JFShell keeps its settings as nested dictionaries. This module maps those
dictionaries to KDL and back, with no third-party dependency.

Mapping
-------
* ``key value``                scalar (str, int, float, bool, null)
* ``key { ... }``              dictionary (``key {}`` is an empty dictionary)
* ``(list)key "a" "b"``        list of scalars (``(list)key`` is an empty list)
* ``(list)key { - ... }``      list holding dictionaries, lists or mixed items

Example::

    "org.jfshell.panel" {
        primary_output {
            name "DP-1"
        }
        top {
            height 32.0
            Exclusive true
        }
    }
    "org.jfshell.plugin.dockbar" {
        (list)pinned "firefox" "kitty"
    }

Reading is lenient: ``//`` and ``/* */`` comments, ``/-`` slashdash, ``;``
terminators, raw strings and v2 style ``#true``/``#false``/``#null`` are
accepted. Writing always produces plain KDL v1.

Node properties (``key=value``) have no equivalent in the data model and are
rejected with a clear error.
"""

import datetime
import re
from typing import Any, Dict, List, Optional, TextIO

CONFIG_FILENAME = "config.kdl"
LEGACY_CONFIG_FILENAME = "config.toml"

_LIST_TYPE = "list"
_ITEM_NAME = "-"

_NEWLINES = frozenset("\n\r\u0085\u000b\u000c\u2028\u2029")
_SPACES = frozenset(
    " \t\u00a0\u1680\u202f\u205f\u3000\ufeff"
    + "".join(chr(c) for c in range(0x2000, 0x200B))
)
_WORD_STOP = _NEWLINES | _SPACES | frozenset('\\/(){}<>;[]=,"')

_RAW_START = re.compile(r'r(#*)"')
_RADIX_INT = re.compile(r"^([+-]?)0([xob])([0-9a-fA-F_]+)$")
_DEC_INT = re.compile(r"^[+-]?\d[\d_]*$")
_FLOAT = re.compile(r"^[+-]?\d[\d_]*(\.\d[\d_]*)?([eE][+-]?\d[\d_]*)?$")
_BARE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")
_KEYWORDS = frozenset({"true", "false", "null"})

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
}
_UNESCAPES = {
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "\\": "\\",
    "/": "/",
    '"': '"',
    "b": "\b",
    "f": "\f",
    "s": " ",
}


class KdlDecodeError(ValueError):
    """Raised when a document is not valid KDL or cannot be mapped to a dict."""


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


class _Node:
    __slots__ = ("name", "type", "args", "props", "children")

    def __init__(self, name: str, typ: Optional[str]) -> None:
        self.name = name
        self.type = typ
        self.args: List[Any] = []
        self.props: Dict[str, Any] = {}
        self.children: Optional[List["_Node"]] = None


class _Parser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.n = len(text)
        self.i = 0

    # -- helpers ------------------------------------------------------------

    def error(self, message: str) -> KdlDecodeError:
        line = self.s.count("\n", 0, self.i) + 1
        column = self.i - (self.s.rfind("\n", 0, self.i) + 1) + 1
        return KdlDecodeError(f"{message} (line {line}, column {column})")

    def peek(self) -> str:
        return self.s[self.i] if self.i < self.n else ""

    def starts(self, token: str) -> bool:
        return self.s.startswith(token, self.i)

    def skip_newline(self) -> None:
        if self.starts("\r\n"):
            self.i += 2
        else:
            self.i += 1

    def skip_block_comment(self) -> None:
        depth = 0
        while self.i < self.n:
            if self.starts("/*"):
                depth += 1
                self.i += 2
            elif self.starts("*/"):
                depth -= 1
                self.i += 2
                if depth == 0:
                    return
            else:
                self.i += 1
        raise self.error("Unterminated block comment")

    def skip_line_comment(self) -> None:
        while self.i < self.n and self.s[self.i] not in _NEWLINES:
            self.i += 1

    def skip_ws(self) -> None:
        """Skip spaces, block comments and escaped line breaks (not newlines)."""
        while self.i < self.n:
            c = self.s[self.i]
            if c in _SPACES:
                self.i += 1
            elif self.starts("/*"):
                self.skip_block_comment()
            elif c == "\\":
                start = self.i
                self.i += 1
                while self.i < self.n and (
                    self.s[self.i] in _SPACES or self.starts("/*")
                ):
                    if self.starts("/*"):
                        self.skip_block_comment()
                    else:
                        self.i += 1
                if self.starts("//"):
                    self.skip_line_comment()
                if self.i < self.n and self.s[self.i] in _NEWLINES:
                    self.skip_newline()
                elif self.i < self.n:
                    self.i = start
                    raise self.error("Unexpected '\\'")
            else:
                break

    def skip_linespace(self) -> None:
        """Skip whitespace, newlines, comments and stray ';' between nodes."""
        while self.i < self.n:
            self.skip_ws()
            c = self.peek()
            if c in _NEWLINES and c:
                self.skip_newline()
            elif c == ";":
                self.i += 1
            elif self.starts("//"):
                self.skip_line_comment()
            else:
                break

    # -- tokens -------------------------------------------------------------

    def parse_bare_word(self) -> str:
        start = self.i
        while self.i < self.n and self.s[self.i] not in _WORD_STOP:
            self.i += 1
        if self.i == start:
            raise self.error(f"Unexpected character {self.peek()!r}")
        return self.s[start : self.i]

    def parse_quoted(self) -> str:
        self.i += 1  # opening quote
        out: List[str] = []
        while self.i < self.n:
            c = self.s[self.i]
            if c == '"':
                self.i += 1
                return "".join(out)
            if c != "\\":
                out.append(c)
                self.i += 1
                continue
            self.i += 1
            e = self.peek()
            if e in _UNESCAPES and e:
                out.append(_UNESCAPES[e])
                self.i += 1
            elif e == "u" and self.s.startswith("{", self.i + 1):
                end = self.s.find("}", self.i)
                if end == -1:
                    raise self.error("Unterminated unicode escape")
                try:
                    out.append(chr(int(self.s[self.i + 2 : end], 16)))
                except (ValueError, OverflowError):
                    raise self.error("Invalid unicode escape") from None
                self.i = end + 1
            elif e and (e in _SPACES or e in _NEWLINES):
                while self.i < self.n and (
                    self.s[self.i] in _SPACES or self.s[self.i] in _NEWLINES
                ):
                    self.i += 1
            else:
                raise self.error(f"Invalid escape '\\{e}'")
        raise self.error("Unterminated string")

    def parse_raw(self) -> str:
        m = _RAW_START.match(self.s, self.i)
        assert m is not None
        closing = '"' + m.group(1)
        start = m.end()
        end = self.s.find(closing, start)
        if end == -1:
            raise self.error("Unterminated raw string")
        self.i = end + len(closing)
        return self.s[start:end]

    def at_string(self) -> bool:
        return self.peek() == '"' or bool(_RAW_START.match(self.s, self.i))

    def parse_string(self) -> str:
        if self.peek() == '"':
            if self.starts('"""'):
                raise self.error("Multi-line strings are not supported")
            return self.parse_quoted()
        return self.parse_raw()

    def parse_type(self) -> str:
        self.i += 1  # '('
        self.skip_ws()
        name = self.parse_string() if self.at_string() else self.parse_bare_word()
        self.skip_ws()
        if self.peek() != ")":
            raise self.error("Expected ')' after type annotation")
        self.i += 1
        return name

    def convert_bare(self, word: str) -> Any:
        if word in ("true", "#true"):
            return True
        if word in ("false", "#false"):
            return False
        if word in ("null", "#null"):
            return None
        m = _RADIX_INT.fullmatch(word)
        if m:
            base = {"x": 16, "o": 8, "b": 2}[m.group(2)]
            try:
                value = int(m.group(3).replace("_", ""), base)
            except ValueError:
                return word
            return -value if m.group(1) == "-" else value
        if _DEC_INT.fullmatch(word):
            return int(word.replace("_", ""))
        if _FLOAT.fullmatch(word):
            return float(word.replace("_", ""))
        return word  # lenient: bare string value

    def parse_value_token(self) -> Any:
        if self.peek() == "(":
            self.parse_type()  # value annotations are accepted and ignored
        if self.at_string():
            return self.parse_string()
        return self.convert_bare(self.parse_bare_word())

    # -- structure ----------------------------------------------------------

    def parse_entry(self, node: _Node) -> None:
        typ = None
        if self.peek() == "(":
            typ = self.parse_type()
        if self.at_string():
            token: Any = self.parse_string()
            is_text = True
        else:
            word = self.parse_bare_word()
            token = word
            is_text = False
        if self.peek() == "=":
            if typ:
                raise self.error("Property keys cannot have a type annotation")
            self.i += 1
            node.props[token] = self.parse_value_token()
            return
        node.args.append(token if is_text else self.convert_bare(token))

    def parse_children(self, depth: int) -> List[_Node]:
        self.i += 1  # '{'
        nodes = self.parse_nodes(depth + 1)
        if self.peek() != "}":
            raise self.error("Expected '}'")
        self.i += 1
        return nodes

    def parse_node(self, depth: int) -> _Node:
        typ = self.parse_type() if self.peek() == "(" else None
        name = self.parse_string() if self.at_string() else self.parse_bare_word()
        node = _Node(name, typ)
        while True:
            self.skip_ws()
            c = self.peek()
            if c == "" or c in _NEWLINES or c == ";" or c == "}" or self.starts("//"):
                break
            if c == "{":
                if node.children is not None:
                    raise self.error("A node can only have one children block")
                node.children = self.parse_children(depth)
                continue
            if self.starts("/-"):
                self.i += 2
                self.skip_ws()
                if self.peek() == "{":
                    self.parse_children(depth)
                else:
                    self.parse_entry(_Node("", None))
                continue
            if node.children is not None:
                raise self.error("Unexpected content after children block")
            self.parse_entry(node)
        return node

    def parse_nodes(self, depth: int) -> List[_Node]:
        nodes: List[_Node] = []
        while True:
            self.skip_linespace()
            if self.i >= self.n:
                if depth:
                    raise self.error("Unexpected end of input, missing '}'")
                return nodes
            if self.peek() == "}":
                if not depth:
                    raise self.error("Unexpected '}'")
                return nodes
            slashdash = False
            if self.starts("/-"):
                self.i += 2
                self.skip_linespace()
                slashdash = True
            node = self.parse_node(depth)
            if not slashdash:
                nodes.append(node)


def _node_value(node: _Node, where: str) -> Any:
    if node.props:
        raise KdlDecodeError(
            f"Node '{where}' uses properties ({', '.join(node.props)}=...), "
            "which are not supported; write each as a child node instead"
        )
    if node.type == _LIST_TYPE:
        if node.children is not None:
            items = []
            for child in node.children:
                if child.name != _ITEM_NAME:
                    raise KdlDecodeError(
                        f"List '{where}' can only contain '-' items, "
                        f"found '{child.name}'"
                    )
                items.append(_node_value(child, f"{where}[{len(items)}]"))
            return items
        return list(node.args)
    if node.children is not None:
        if node.args:
            raise KdlDecodeError(
                f"Node '{where}' has both arguments and children; "
                "use '(list)' for lists"
            )
        return _nodes_to_dict(node.children, where)
    if len(node.args) == 1:
        return node.args[0]
    if not node.args:
        return {}
    return list(node.args)


def _nodes_to_dict(nodes: List[_Node], where: str = "") -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for node in nodes:
        path = f"{where}.{node.name}" if where else node.name
        if node.name in result:
            raise KdlDecodeError(f"Duplicate key '{path}'")
        result[node.name] = _node_value(node, path)
    return result


def loads(text: str) -> Dict[str, Any]:
    """Parse a KDL document into a dictionary."""
    parser = _Parser(text)
    return _nodes_to_dict(parser.parse_nodes(0))


def load(fp: TextIO) -> Dict[str, Any]:
    """Parse a KDL document from an open text file."""
    return loads(fp.read())


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def _quote(text: str) -> str:
    out = ['"']
    for ch in text:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F or ch in "\u2028\u2029":
            out.append(f"\\u{{{ord(ch):x}}}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _name(key: Any) -> str:
    text = str(key)
    if _BARE_NAME.fullmatch(text) and text not in _KEYWORDS:
        return text
    return _quote(text)


def _scalar(value: Any, path: str) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"Cannot store {value!r} at '{path}' in KDL")
        return repr(value)
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return _quote(value.isoformat())
    raise TypeError(f"Unsupported value type {type(value).__name__} at '{path}'")


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, (dict, list, tuple))


def _emit(name: str, value: Any, indent: int, path: str, out: List[str]) -> None:
    pad = "    " * indent
    if isinstance(value, dict):
        if not value:
            out.append(f"{pad}{name} {{}}")
            return
        out.append(f"{pad}{name} {{")
        for key, item in value.items():
            _emit(_name(key), item, indent + 1, f"{path}.{key}", out)
        out.append(f"{pad}}}")
    elif isinstance(value, (list, tuple)):
        if all(_is_scalar(item) for item in value):
            args = "".join(f" {_scalar(item, path)}" for item in value)
            out.append(f"{pad}({_LIST_TYPE}){name}{args}")
            return
        out.append(f"{pad}({_LIST_TYPE}){name} {{")
        for index, item in enumerate(value):
            _emit(_ITEM_NAME, item, indent + 1, f"{path}[{index}]", out)
        out.append(f"{pad}}}")
    else:
        out.append(f"{pad}{name} {_scalar(value, path)}")


def dumps(data: Dict[str, Any]) -> str:
    """Serialize a dictionary to a KDL document."""
    if not isinstance(data, dict):
        raise TypeError("KDL documents must be built from a dictionary")
    out: List[str] = []
    for key, value in data.items():
        if out and isinstance(value, dict):
            out.append("")
        _emit(_name(key), value, 0, str(key), out)
    return "\n".join(out) + "\n" if out else ""


def dump(data: Dict[str, Any], fp: TextIO) -> None:
    """Serialize a dictionary to an open text file.

    The whole document is built before anything is written, so a value that
    cannot be stored never leaves a truncated file behind.
    """
    fp.write(dumps(data))


# --------------------------------------------------------------------------- #
# Migration from the previous TOML based configuration
# --------------------------------------------------------------------------- #


def migrate_legacy_toml(kdl_path: Any) -> bool:
    """Create ``kdl_path`` from a sibling TOML file when the KDL one is absent.

    The TOML file is left untouched. A missing or empty ``kdl_path`` counts as
    absent. Returns True when a conversion was written.
    """
    import os
    import tomllib

    kdl_file = os.fspath(kdl_path)
    legacy_file = os.path.join(
        os.path.dirname(kdl_file),
        os.path.basename(kdl_file)[: -len(".kdl")] + ".toml",
    )
    if not kdl_file.endswith(".kdl") or not os.path.isfile(legacy_file):
        return False
    if os.path.isfile(kdl_file) and os.path.getsize(kdl_file) > 0:
        return False
    with open(legacy_file, "rb") as f:
        data = tomllib.load(f)
    text = dumps(data)
    tmp_file = kdl_file + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp_file, kdl_file)
    return True
