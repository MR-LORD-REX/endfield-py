"""Formatting-preserving JSON document editing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class Node:
    kind: str
    start: int
    end: int
    value: Any
    members: dict[str, "Member"] | None = None
    items: list["Node"] | None = None


@dataclass
class Member:
    key: str
    key_start: int
    key_end: int
    value: Node


class Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.length = len(text)

    def parse(self) -> Node:
        node, index = self.value(0)
        index = self.skip(index)
        if index != self.length:
            raise ValueError(f"Unexpected trailing content at {index}")
        return node

    def skip(self, index: int) -> int:
        while index < self.length and self.text[index] in " \t\r\n":
            index += 1
        return index

    def string(self, index: int) -> tuple[str, int]:
        start = index
        if self.text[index] != '"':
            raise ValueError(f"Expected string at {index}")
        index += 1
        while index < self.length:
            char = self.text[index]
            if char == "\\":
                index += 2
                continue
            if char == '"':
                return json.loads(self.text[start:index + 1]), index + 1
            index += 1
        raise ValueError("Unterminated string")

    def value(self, index: int) -> tuple[Node, int]:
        index = self.skip(index)
        char = self.text[index]
        if char == "{":
            return self.object(index)
        if char == "[":
            return self.array(index)
        if char == '"':
            value, end = self.string(index)
            return Node("scalar", index, end, value), end
        start = index
        while index < self.length and self.text[index] not in ",]} \t\r\n":
            index += 1
        literal = self.text[start:index]
        value = json.loads(literal)
        return Node("scalar", start, index, value), index

    def object(self, index: int) -> tuple[Node, int]:
        start = index
        index = self.skip(index + 1)
        members: dict[str, Member] = {}
        if self.text[index] == "}":
            return Node("object", start, index + 1, {}, members), index + 1
        while True:
            index = self.skip(index)
            key_start = index
            key, index = self.string(index)
            key_end = index
            index = self.skip(index)
            if self.text[index] != ":":
                raise ValueError(f"Expected : at {index}")
            child, index = self.value(index + 1)
            members[key] = Member(key, key_start, key_end, child)
            index = self.skip(index)
            char = self.text[index]
            if char == ",":
                index += 1
                continue
            if char == "}":
                value = {key: member.value for key, member in members.items()}
                return Node("object", start, index + 1, value, members), index + 1
            raise ValueError(f"Expected , or }} at {index}")

    def array(self, index: int) -> tuple[Node, int]:
        start = index
        index = self.skip(index + 1)
        items: list[Node] = []
        if self.text[index] == "]":
            return Node("array", start, index + 1, [], None, items), index + 1
        while True:
            child, index = self.value(index)
            items.append(child)
            index = self.skip(index)
            char = self.text[index]
            if char == ",":
                index += 1
                continue
            if char == "]":
                return Node("array", start, index + 1, [item.value for item in items], None, items), index + 1
            raise ValueError(f"Expected , or ] at {index}")


def parse(text: str) -> Node:
    return Parser(text).parse()


def line_indent(text: str, index: int) -> str:
    line_start = text.rfind("\n", 0, index) + 1
    prefix = text[line_start:index]
    return prefix if not prefix.strip() else ""


def value_indent(text: str, node: Node) -> str:
    """Indentation of the line containing a value, independent of its column."""
    line_start = text.rfind("\n", 0, node.start) + 1
    index = line_start
    while index < len(text) and text[index] in " \t":
        index += 1
    return text[line_start:index]


def object_style(text: str, node: Node) -> tuple[str, str]:
    if node.members:
        first = next(iter(node.members.values()))
        line_start = text.rfind("\n", 0, first.key_start) + 1
        prefix = text[line_start:first.key_start]
        if prefix.strip() == "" and line_start > node.start:
            return prefix, "\n"
    return value_indent(text, node) + "    ", " "


def array_style(text: str, node: Node) -> tuple[str, str]:
    if node.items:
        first = node.items[0]
        line_start = text.rfind("\n", 0, first.start) + 1
        prefix = text[line_start:first.start]
        if prefix.strip() == "" and line_start > node.start:
            return prefix, "\n"
    return "", " "


def scalar_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return left == right


def render_scalar(text: str, node: Node, value: Any, key: str = "") -> str:
    if scalar_equal(node.value, value):
        return text[node.start:node.end]
    if isinstance(value, str):
        if isinstance(node.value, str) and json.loads(text[node.start:node.end]) == value:
            return text[node.start:node.end]
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def indented(rendered: str, indent: str) -> str:
    lines = rendered.splitlines()
    if len(lines) <= 1:
        return rendered
    return lines[0] + "".join("\n" + indent + line for line in lines[1:])


def patch(text: str, value: Any) -> str:
    root = parse(text)
    patched = patch_node(text, root, value)
    return patched + text[root.end:]


def patch_node(text: str, node: Node, value: Any, key: str = "") -> str:
    if isinstance(value, dict) and node.kind == "object":
        return patch_object(text, node, value)
    if isinstance(value, list) and node.kind == "array":
        return patch_array(text, node, value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=4, allow_nan=False)
    return render_scalar(text, node, value, key)


def patch_object(text: str, node: Node, value: dict[str, Any]) -> str:
    members = node.members or {}
    indent, layout = object_style(text, node)
    if layout == "\n" and not indent:
        indent = value_indent(text, node) + "    "
    keys = [key for key in members if key in value] + [key for key in value if key not in members]
    if not keys:
        return "{}"
    parts: list[str] = []
    for key in keys:
        if key in members:
            member = members[key]
            rendered = patch_node(text, member.value, value[key], key)
            parts.append(f"{text[member.key_start:member.key_end]}: {rendered}")
        else:
            rendered = indented(json.dumps(value[key], ensure_ascii=False, indent=4, allow_nan=False), indent)
            parts.append(f"{json.dumps(key, ensure_ascii=False)}: {rendered}")
    if layout == "\n":
        return "{\n" + indent + (",\n" + indent).join(parts) + "\n" + value_indent(text, node) + "}"
    return "{" + ", ".join(parts) + "}"


def patch_array(text: str, node: Node, value: list[Any]) -> str:
    items = node.items or []
    indent, layout = array_style(text, node)
    parts: list[str] = []
    for position, item in enumerate(value):
        if position < len(items):
            parts.append(patch_node(text, items[position], item))
        else:
            parts.append(indented(json.dumps(item, ensure_ascii=False, indent=4, allow_nan=False), indent))
    if not parts:
        return "[]"
    if layout == "\n":
        return "[\n" + indent + (",\n" + indent).join(parts) + "\n" + value_indent(text, node) + "]"
    return "[" + ", ".join(parts) + "]"
