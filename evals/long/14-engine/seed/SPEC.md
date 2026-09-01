# Twig

Twig is a rendering engine for a small subset of HTML and CSS. It reads one HTML file and
prints a box tree: where every box ended up and how big it is. There is no painting, no
colour and no font file -- the geometry *is* the output, which is what makes it checkable.

    python3 render.py PAGE.html [--width N]

`--width` is the viewport width in pixels and defaults to `800`. The box tree goes to
stdout. A document with no `<body>` prints nothing and exits 0.

## Rules that hold everywhere

These nine are stated once, here, and are not repeated where they apply.

1. **Every length is an integer number of pixels.** A length is an optional `-`, digits, and
   an optional `px` suffix: `10px`, `10`, `0` are all lengths. Wherever a computation
   divides, it **truncates toward zero** -- `15 / 2` is `7`.
2. **A negative length is invalid.** The declaration containing it is ignored entirely, and
   whatever the property would otherwise have been -- an earlier declaration, or the initial
   value -- still stands.
3. **`&nbsp;` is not whitespace.** It is the character U+00A0. It never collapses with the
   spaces around it and a line never breaks at one. `&#160;` is the same character.
4. **A `style` attribute beats every selector**, however specific. `!important` beats every
   declaration that is not `!important`, including one in a `style` attribute.
5. **Equal weight and equal specificity go to the later declaration**, in source order.
6. **Margins collapse only between adjacent siblings.** A parent never collapses with its
   first or last child, so a child's top margin always pushes it down inside its parent, and
   a last child's bottom margin always adds to its parent's height.
7. **A word too wide for its line gets a line of its own and overflows it.** A line box's
   width is what it measured, which may be larger than the block it is in.
8. **A line box is as tall as the tallest word on it** -- the largest `font-size + 4` among
   them. A line with no words on it is `font-size + 4` for the *block's* font size.
9. **Anything unrecognised is ignored, as locally as possible.** An unknown property, or a
   value that will not parse, drops that one declaration and leaves the rest of its rule
   working.

## Parsing HTML

Tag names and attribute names are lowercased. Attribute values are taken as written and may
be unquoted, single-quoted or double-quoted; a bare attribute has the value `""`. A trailing
`/` in a start tag is ignored, so `<br/>` and `<br>` are the same.

`<!-- comments -->` and `<!DOCTYPE ...>` are discarded. The content of `<script>` is
discarded with it. The content of `<style>` is CSS, not text -- see below.

These elements never have children and never need closing:

    area base br col embed hr img input link meta param source track wbr

A `<p>` start tag closes an open `<p>`. A `<li>` start tag closes an open `<li>`. No other
element closes another.

An end tag closes the nearest open element with that name, along with anything still open
inside it. An end tag matching nothing open is ignored. Anything still open at the end of the
file is closed there.

These character references are recognised in text and in attribute values:

    &amp;  &lt;  &gt;  &quot;  &apos;  &nbsp;  and decimal &#NNN;

Anything else beginning with `&` is left exactly as it was written.

## Parsing CSS

The stylesheet is the content of every `<style>` element, concatenated in document order.
`/* ... */` comments are removed first, anywhere they appear.

A rule is a selector list, `{`, declarations, `}`. A declaration is `property: value`, and
they are separated by `;`; the last one may omit it. A value ending in `!important` is an
important declaration and the marker is not part of the value.

A selector list is selectors separated by `,`. A selector is one or more compound selectors
separated by whitespace, which is the descendant combinator: `div p` matches any `p` with a
`div` anywhere above it. A compound selector is an optional tag name followed by any number
of `.class` and `#id` parts, or the single character `*`. A selector that will not parse is
dropped on its own; the others in the same list still apply.

Specificity is counted over the whole selector as `(ids, classes, tags)` and compared in that
order. `*` contributes nothing.

## The cascade

For each element and each property, consider every declaration that applies: those from
rules whose selector matches, and those in the element's own `style` attribute. Rank them by,
in order: `!important` first, then specificity, then source order (rule 5). The highest wins.

If nothing applies, the property takes its **initial** value -- except `font-size`, which is
the only inherited property: an element with no `font-size` of its own takes its parent's.

| property | initial |
|---|---|
| `display` | `block` for the elements listed below, `inline` for every other |
| `width`, `height` | `auto` |
| `margin-top`, `margin-right`, `margin-bottom`, `margin-left` | `0` |
| `padding-top`, `padding-right`, `padding-bottom`, `padding-left` | `0` |
| `border-width` | `0` |
| `font-size` | `16` (inherited) |

Elements whose initial `display` is `block`:

    body div p h1 h2 h3 h4 h5 h6 ul ol li section article header footer main blockquote

`display` takes `block`, `inline` or `none`. An element with `display: none` is not
rendered, and neither is anything inside it.

`margin` and `padding` are shorthands taking one to four lengths: one sets all four sides;
two are vertical then horizontal; three are top, horizontal, bottom; four are top, right,
bottom, left. `border-width` sets all four sides at once. `width` and `height` take a length
or `auto`. `font-size` takes a length. Every other property is ignored (rule 9).

## Boxes

Each rendered element with `display: block` is a block box. Outward from the middle it has a
content area, then padding, then a border, then margin.

    content width  = the `width`, or, when `auto`:
                     containing content width - margin-left - border - padding-left
                                              - padding-right - border - margin-right
                     with a floor of 0
    content height = the `height`, or, when `auto`, whatever the content came to

A block's children are either **all block-level** or **all inline-level**. A document that
mixes both inside one block is not valid input and no case contains one.

## Laying out blocks

The body's margin box starts at `0,0` and its containing content width is the viewport width.

Inside a block, its block children stack down its content area:

* The **first** child's *margin* edge sits at the parent's content top (rule 6).
* Each **later** child's *border* edge sits below the previous child's border edge by
  `max(previous child's margin-bottom, this child's margin-top)` (rule 6).
* A block's `auto` height runs from its content top to the **margin** edge below its last
  child (rule 6). With no children it is `0`.

Every child gets the parent's content width as its containing width, and its content left
edge is `parent content x + margin-left + border-width + padding-left`.

## Laying out text

The inline content of a block is read in document order as a stream of characters, each
carrying the `font-size` of the element it came from, with `<br>` marking a forced break.

A **word** is a run of characters with no ASCII space, tab, newline, carriage return or form
feed in it (rule 3). Element boundaries do not end a word: `a<span>b</span>c` is one word
`abc`, and so is `keep<span style="display: none">x</span>me` -- `keepme`.

    character advance = that character's font-size / 2       (rule 1)
    word width        = the sum of its characters' advances
    space width       = the block's own font-size / 2

Words fill lines greedily, left to right. A word goes on the current line if putting it there
-- with one space before it, unless it is first -- would not take the line past the block's
content width. Otherwise it starts a new line (rule 7).

`<br>` ends the current line immediately. The next word starts a new one. Two in a row leave
an empty line between them; one at the very end adds no line.

A line box starts at the block's content left edge, is as wide as it measured, and is as tall
as rule 8 says. The block's lines stack downwards from its content top, and an `auto` height
block is as tall as its lines together.

## The output

One line per box, two spaces of indent per level of nesting, starting at the body:

    BLOCK <tag> <x>,<y> <width>x<height>
    LINE <x>,<y> <width>x<height> "<text>"

`x,y` is the **top-left of the content area** and `width`x`height` is the **content size** --
padding, border and margin show up only in where things ended up.

Only block boxes and line boxes appear. An inline element never prints a line of its own; its
text is in the line boxes of the block that contains it.

A line's text is its words joined by single spaces. It never contains a `"`.

    BLOCK body 0,0 800x70
      BLOCK p 10,10 780x20
        LINE 10,10 496x20 "Hello world this is a test"
