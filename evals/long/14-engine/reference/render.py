#!/usr/bin/env python3
"""Reference implementation of Twig, kept beside the rung and never shipped in its seed.

This exists to generate `cases/*.out` and to prove the rung is passable. It is deliberately
plain: every rule in SPEC.md is one obvious piece of code here, so that when a case looks
wrong the question "what does the spec say" and the question "what does this do" have the
same answer in the same place.

Usage: python3 render.py PAGE.html [--width N]
"""

from __future__ import annotations

import sys

VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
CLOSES_SELF = {"p": {"p"}, "li": {"li"}}
BLOCK_TAGS = {
    "body", "div", "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "section", "article", "header", "footer", "main", "blockquote",
}
ENTITIES = {
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": "\u00a0",
}
INHERITED = {"font-size"}
INITIAL = {
    "display": None,          # per element, filled in during cascade
    "width": "auto",
    "height": "auto",
    "margin-top": 0, "margin-right": 0, "margin-bottom": 0, "margin-left": 0,
    "padding-top": 0, "padding-right": 0, "padding-bottom": 0, "padding-left": 0,
    "border-width": 0,
    "font-size": 16,
}
SIDES = ("top", "right", "bottom", "left")
#: Only these break a line. `str.split()` also splits on U+00A0, which is the one character
#: that must never break one -- so the splitting is written out rather than borrowed.
ASCII_SPACE = " \t\n\r\f"


# ---------------------------------------------------------------------------- HTML


class Element:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list = []
        self.parent: Element | None = None


def unescape(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char != "&":
            out.append(char)
            index += 1
            continue
        end = text.find(";", index, index + 12)
        if end == -1:
            out.append(char)
            index += 1
            continue
        body = text[index + 1 : end]
        if body.startswith("#") and body[1:].isdigit():
            out.append(chr(int(body[1:])))
        elif body in ENTITIES:
            out.append(ENTITIES[body])
        else:
            out.append(text[index : end + 1])
            index = end + 1
            continue
        index = end + 1
    return "".join(out)


def words_of(text: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for char in text:
        if char in ASCII_SPACE:
            if current:
                out.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        out.append("".join(current))
    return out


def parse_attributes(source: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    index = 0
    while index < len(source):
        while index < len(source) and source[index].isspace():
            index += 1
        start = index
        while index < len(source) and not source[index].isspace() and source[index] != "=":
            index += 1
        if index == start:
            break
        name = source[start:index].lower()
        while index < len(source) and source[index].isspace():
            index += 1
        if index < len(source) and source[index] == "=":
            index += 1
            while index < len(source) and source[index].isspace():
                index += 1
            if index < len(source) and source[index] in "\"'":
                quote = source[index]
                index += 1
                stop = source.find(quote, index)
                stop = len(source) if stop == -1 else stop
                attrs[name] = unescape(source[index:stop])
                index = stop + 1
            else:
                stop = index
                while stop < len(source) and not source[stop].isspace():
                    stop += 1
                attrs[name] = unescape(source[index:stop])
                index = stop
        else:
            attrs[name] = ""
    return attrs


def parse_html(text: str) -> tuple[Element, str]:
    """The document element, and every `<style>` body concatenated in document order."""
    root = Element("#document", {})
    stack = [root]
    css: list[str] = []
    index = 0

    while index < len(text):
        if text[index] != "<":
            stop = text.find("<", index)
            stop = len(text) if stop == -1 else stop
            stack[-1].children.append(unescape(text[index:stop]))
            index = stop
            continue

        if text.startswith("<!--", index):
            stop = text.find("-->", index)
            index = len(text) if stop == -1 else stop + 3
            continue
        if text.startswith("<!", index):
            stop = text.find(">", index)
            index = len(text) if stop == -1 else stop + 1
            continue

        stop = text.find(">", index)
        if stop == -1:
            break
        inner = text[index + 1 : stop]
        index = stop + 1

        if inner.startswith("/"):
            name = inner[1:].strip().lower()
            for depth in range(len(stack) - 1, 0, -1):
                if stack[depth].tag == name:
                    del stack[depth:]
                    break
            continue

        inner = inner.rstrip("/")
        space = 0
        while space < len(inner) and not inner[space].isspace():
            space += 1
        name = inner[:space].lower()
        if not name:
            continue
        element = Element(name, parse_attributes(inner[space:]))

        if name in CLOSES_SELF and stack[-1].tag in CLOSES_SELF[name]:
            stack.pop()

        # `style` and `script` swallow their contents: neither is markup, and one of them
        # is the stylesheet.
        if name in ("style", "script"):
            closing = f"</{name}"
            stop = text.lower().find(closing, index)
            body = text[index:stop] if stop != -1 else text[index:]
            if name == "style":
                css.append(body)
            index = len(text) if stop == -1 else text.find(">", stop) + 1
            continue

        element.parent = stack[-1]
        stack[-1].children.append(element)
        if name not in VOID:
            stack.append(element)

    return root, "\n".join(css)


def find(node: Element, tag: str) -> Element | None:
    for child in node.children:
        if isinstance(child, Element):
            if child.tag == tag:
                return child
            found = find(child, tag)
            if found is not None:
                return found
    return None


# ---------------------------------------------------------------------------- CSS


class Rule:
    __slots__ = ("selectors", "declarations", "order")

    def __init__(self, selectors: list, declarations: list, order: int) -> None:
        self.selectors = selectors
        self.declarations = declarations
        self.order = order


def parse_css(text: str) -> list[Rule]:
    while "/*" in text:
        start = text.index("/*")
        stop = text.find("*/", start + 2)
        text = text[:start] + (text[stop + 2 :] if stop != -1 else "")

    rules: list[Rule] = []
    index = 0
    order = 0
    while True:
        brace = text.find("{", index)
        if brace == -1:
            break
        close = text.find("}", brace)
        if close == -1:
            break
        selectors = parse_selectors(text[index:brace])
        declarations = parse_declarations(text[brace + 1 : close])
        if selectors and declarations:
            rules.append(Rule(selectors, declarations, order))
            order += 1
        index = close + 1
    return rules


def parse_selectors(text: str) -> list[list[tuple[str, list[str], list[str]]]]:
    selectors = []
    for part in text.split(","):
        compounds = [parse_compound(word) for word in part.split()]
        if compounds and all(compound is not None for compound in compounds):
            selectors.append(compounds)
    return selectors


def parse_compound(word: str):
    """(tag, classes, ids). `tag` is "" for `*` and for a bare `.class`."""
    tag = ""
    classes: list[str] = []
    ids: list[str] = []
    index = 0
    if word.startswith("*"):
        index = 1
    else:
        while index < len(word) and (word[index].isalnum() or word[index] == "-"):
            index += 1
        tag = word[:index].lower()
    while index < len(word):
        marker = word[index]
        index += 1
        start = index
        while index < len(word) and (word[index].isalnum() or word[index] in "-_"):
            index += 1
        name = word[start:index]
        if not name:
            return None
        if marker == ".":
            classes.append(name)
        elif marker == "#":
            ids.append(name)
        else:
            return None
    return (tag, classes, ids)


def parse_declarations(text: str) -> list[tuple[str, str, bool]]:
    out = []
    for chunk in text.split(";"):
        if ":" not in chunk:
            continue
        name, _, value = chunk.partition(":")
        name = name.strip().lower()
        value = value.strip()
        important = False
        if value.lower().endswith("!important"):
            important = True
            value = value[: value.lower().rindex("!important")].strip()
        if name and value:
            out.append((name, value, important))
    return out


def matches(element: Element, compound) -> bool:
    tag, classes, ids = compound
    if tag and element.tag != tag:
        return False
    own = element.attrs.get("class", "").split()
    if any(name not in own for name in classes):
        return False
    return all(element.attrs.get("id", "") == name for name in ids)


def selector_matches(element: Element, compounds) -> bool:
    if not matches(element, compounds[-1]):
        return False
    ancestor = element.parent
    remaining = list(compounds[:-1])
    while remaining and ancestor is not None:
        if matches(ancestor, remaining[-1]):
            remaining.pop()
        ancestor = ancestor.parent
    return not remaining


def specificity(compounds) -> tuple[int, int, int]:
    ids = classes = tags = 0
    for tag, class_names, id_names in compounds:
        ids += len(id_names)
        classes += len(class_names)
        if tag:
            tags += 1
    return (ids, classes, tags)


# ---------------------------------------------------------------------------- values


def length(value: str):
    """An integer number of pixels, or None if this is not a valid length."""
    value = value.strip().lower()
    if value.endswith("px"):
        value = value[:-2].strip()
    if not value.lstrip("-").isdigit():
        return None
    number = int(value)
    return None if number < 0 else number


def expand(name: str, value: str) -> list[tuple[str, object]]:
    """One declaration as the properties it actually sets."""
    if name in ("margin", "padding"):
        parts = value.split()
        if not 1 <= len(parts) <= 4:
            return []
        sizes = [length(part) for part in parts]
        if any(size is None for size in sizes):
            return []
        if len(sizes) == 1:
            top = right = bottom = left = sizes[0]
        elif len(sizes) == 2:
            top = bottom = sizes[0]
            right = left = sizes[1]
        elif len(sizes) == 3:
            top, (right, left), bottom = sizes[0], (sizes[1], sizes[1]), sizes[2]
        else:
            top, right, bottom, left = sizes
        return [
            (f"{name}-top", top), (f"{name}-right", right),
            (f"{name}-bottom", bottom), (f"{name}-left", left),
        ]
    if name == "display":
        return [("display", value.strip().lower())] if value.strip().lower() in (
            "block", "inline", "none"
        ) else []
    if name in ("width", "height"):
        if value.strip().lower() == "auto":
            return [(name, "auto")]
        size = length(value)
        return [(name, size)] if size is not None else []
    if name in (
        "margin-top", "margin-right", "margin-bottom", "margin-left",
        "padding-top", "padding-right", "padding-bottom", "padding-left",
        "border-width", "font-size",
    ):
        size = length(value)
        return [(name, size)] if size is not None else []
    return []


def cascade(root: Element, rules: list[Rule]) -> dict[int, dict]:
    styles: dict[int, dict] = {}

    def visit(element: Element, inherited: dict) -> None:
        # (important, specificity, order) per property, so a later or stronger declaration
        # replaces an earlier or weaker one and nothing else does.
        winners: dict[str, tuple] = {}
        for rule in rules:
            for compounds in rule.selectors:
                if not selector_matches(element, compounds):
                    continue
                rank = specificity(compounds)
                for name, value, important in rule.declarations:
                    for prop, resolved in expand(name, value):
                        key = (1 if important else 0, rank, rule.order)
                        if prop not in winners or winners[prop][0] <= key:
                            winners[prop] = (key, resolved)
        for name, value, important in parse_declarations(element.attrs.get("style", "")):
            for prop, resolved in expand(name, value):
                # A style attribute outranks every selector, and stays below !important.
                key = (1 if important else 0, (999, 999, 999), 10**9)
                if prop not in winners or winners[prop][0] <= key:
                    winners[prop] = (key, resolved)

        style = dict(INITIAL)
        style["display"] = "block" if element.tag in BLOCK_TAGS else "inline"
        for prop in INHERITED:
            style[prop] = inherited.get(prop, INITIAL[prop])
        for prop, (_, value) in winners.items():
            style[prop] = value
        styles[id(element)] = style

        for child in element.children:
            if isinstance(child, Element):
                visit(child, style)

    for child in root.children:
        if isinstance(child, Element):
            visit(child, dict(INITIAL))
    return styles


# ---------------------------------------------------------------------------- layout


class Box:
    __slots__ = ("kind", "tag", "x", "y", "width", "height", "text", "children")

    def __init__(self, kind, tag, x, y, width, height, text="") -> None:
        self.kind, self.tag = kind, tag
        self.x, self.y, self.width, self.height = x, y, width, height
        self.text = text
        self.children: list[Box] = []


def rendered_children(element: Element, styles) -> list:
    out = []
    for child in element.children:
        if isinstance(child, str) or styles[id(child)]["display"] != "none":
            out.append(child)
    return out


def has_block_child(element: Element, styles) -> bool:
    return any(
        isinstance(child, Element) and styles[id(child)]["display"] == "block"
        for child in rendered_children(element, styles)
    )


def collect_inline(element: Element, styles, size: int) -> list:
    """Inline content as `(character, font-size)` pairs, with `None` for a forced break.

    Character by character rather than word by word, because an element boundary does not
    end a word: `a<span>b</span>c` is the single word `abc`, and `keep<span
    style="display:none">x</span>me` is `keepme`. Collecting words per text node instead
    produces `keep me`, which is the wrong answer and the reason this is written this way.
    """
    items: list = []
    for child in rendered_children(element, styles):
        if isinstance(child, str):
            items.extend((character, size) for character in child)
        elif child.tag == "br":
            items.append(None)
        else:
            items.extend(collect_inline(child, styles, styles[id(child)]["font-size"]))
    return items


def split_words(items: list) -> list:
    """`("word", [(char, size), ...])` and `("break", None)`, in order."""
    out: list = []
    current: list = []
    for item in items:
        if item is None:
            if current:
                out.append(("word", current))
                current = []
            out.append(("break", None))
        elif item[0] in ASCII_SPACE:
            if current:
                out.append(("word", current))
                current = []
        else:
            current.append(item)
    if current:
        out.append(("word", current))
    return out


def lay_out_lines(element: Element, styles, x: int, y: int, width: int) -> list[Box]:
    style = styles[id(element)]
    block_size = style["font-size"]
    space = block_size // 2

    lines: list[Box] = []
    words: list[str] = []
    used = 0
    tallest = 0
    cursor = y

    def flush() -> None:
        nonlocal words, used, tallest, cursor
        height = tallest if words else block_size + 4
        lines.append(Box("LINE", "", x, cursor, used, height, " ".join(words)))
        cursor += height
        words, used, tallest = [], 0, 0

    for kind, payload in split_words(collect_inline(element, styles, block_size)):
        if kind == "break":
            flush()
            continue
        advance = sum(size // 2 for _, size in payload)
        rise = max(size + 4 for _, size in payload)
        extra = advance if not words else space + advance
        if words and used + extra > width:
            flush()
            extra = advance
        words.append("".join(character for character, _ in payload))
        used += extra
        tallest = max(tallest, rise)
    if words:
        flush()
    return lines


def lay_out_block(element: Element, styles, x: int, y: int, available: int) -> Box:
    """`x, y` is the margin-box left and top. Returns the box, sized and placed."""
    style = styles[id(element)]
    border = style["border-width"]
    pad = {side: style[f"padding-{side}"] for side in SIDES}
    margin = {side: style[f"margin-{side}"] for side in SIDES}

    if style["width"] == "auto":
        content_width = available - (
            margin["left"] + margin["right"] + 2 * border + pad["left"] + pad["right"]
        )
        content_width = max(0, content_width)
    else:
        content_width = style["width"]

    content_x = x + margin["left"] + border + pad["left"]
    content_y = y + margin["top"] + border + pad["top"]

    children: list[Box] = []
    if has_block_child(element, styles):
        cursor = content_y
        previous_bottom = None
        for child in rendered_children(element, styles):
            if not isinstance(child, Element):
                continue
            child_style = styles[id(child)]
            gap = 0 if previous_bottom is None else max(
                previous_bottom, child_style["margin-top"]
            )
            # `lay_out_block` takes a margin-box top, so the collapsed gap is applied to
            # the border edge and the child's own top margin taken back off.
            top = (
                content_y
                if previous_bottom is None
                else cursor + gap - child_style["margin-top"]
            )
            box = lay_out_block(child, styles, content_x, top, content_width)
            children.append(box)
            previous_bottom = child_style["margin-bottom"]
            cursor = _border_bottom(child, box, styles)
        content_height = (
            0 if previous_bottom is None else (cursor + previous_bottom) - content_y
        )
    else:
        children = lay_out_lines(element, styles, content_x, content_y, content_width)
        content_height = sum(line.height for line in children)

    if style["height"] != "auto":
        content_height = style["height"]

    box = Box("BLOCK", element.tag, content_x, content_y, content_width, content_height)
    box.children = children
    return box


def _border_bottom(element: Element, box: Box, styles) -> int:
    style = styles[id(element)]
    return box.y + box.height + style["padding-bottom"] + style["border-width"]


def dump(box: Box, depth: int, out: list[str]) -> None:
    pad = "  " * depth
    if box.kind == "BLOCK":
        out.append(f"{pad}BLOCK {box.tag} {box.x},{box.y} {box.width}x{box.height}")
    else:
        out.append(f'{pad}LINE {box.x},{box.y} {box.width}x{box.height} "{box.text}"')
    for child in box.children:
        dump(child, depth + 1, out)


def main(argv: list[str]) -> int:
    width = 800
    paths = []
    index = 0
    while index < len(argv):
        if argv[index] == "--width":
            width = int(argv[index + 1])
            index += 2
        else:
            paths.append(argv[index])
            index += 1
    if not paths:
        print("usage: render.py PAGE.html [--width N]", file=sys.stderr)
        return 2

    with open(paths[0], encoding="utf-8") as handle:
        text = handle.read()
    document, css = parse_html(text)
    styles = cascade(document, parse_css(css))
    body = find(document, "body")
    if body is None or styles[id(body)]["display"] == "none":
        return 0

    box = lay_out_block(body, styles, 0, 0, width)
    out: list[str] = []
    dump(box, 0, out)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
