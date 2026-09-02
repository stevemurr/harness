// Everything both pages do with a transcript: rendering it, measuring it, and showing
// what a tool actually returned. Included into each page at request time rather than
// fetched -- see `server.page`, which explains why this is not a <script src>.

const $ = (id) => document.getElementById(id);
const el = (cls, text) => { const d = document.createElement("div"); d.className = cls; if (text) d.textContent = text; return d; };
const scroller = () => document.querySelector("main");
const near = () => {
  const box = scroller();
  return box.scrollHeight - box.scrollTop - box.clientHeight < 160;
};
const brief = (n) => n >= 1024 ? `${Math.round(n / 1024)}K` : String(n);

function human(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

const CHARS_PER_TOKEN = 3.5;   // settings.py: measured 3.4-3.5 against a live Qwen3
const KEEP_TURNS = 2;          // Compaction.keep_turns
const FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏";
let ctxWindow = 262144, compactAt = 0.8;
const messages = [];

// ---------------------------------------------------------------------------- markdown
// Hand-written, for the reason the rest of this harness is hand-written: the whole job is a
// dozen block shapes, and the alternative is a CDN script on a page that has no build step
// and is meant to work with the network down.
//
// It builds nodes and never assigns innerHTML. That is not fastidiousness. The agent has
// open_url, so a tool result can carry text lifted off a hostile page, and the model relays
// it into its own prose. A renderer that took the innerHTML shortcut would run a stranger's
// script on this origin -- the origin holding the buttons that approve shell commands.
const SAFE_LINK = /^(https?:\/\/|mailto:)/i;
// `_em_` and `__bold__` are deliberately not supported: they turn snake_case_identifiers
// into italics, and this is a page for reading about code.
const INLINE = /(`+)([\s\S]*?)\1|\*\*([\s\S]+?)\*\*|\*([^\s*][^*]*?)\*|\[([^\]]*)\]\(([^\s)]+)\)/g;
const BLOCK = /^(\s*(?:```|~~~)|#{1,6}\s|\s*>|\s*(?:[-*+]|\d+[.)])\s|\s*\|)/;

// `markdown` answers with a `.md` box, and nesting that box inside a list item or a quote
// would leave a wrapper carrying its own margins in the middle of one. Move the children
// across instead, so a nested list sits directly in its `<li>`.
function absorb(into, box) {
  while (box.firstChild) into.appendChild(box.firstChild);
  return into;
}

function nest(into, tag) {
  const node = document.createElement(tag);
  into.appendChild(node);
  return node;
}

function put(into, tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  into.appendChild(node);
}

// Emphasis nests, so this recurses -- and that is why the pattern is rebuilt here rather
// than shared. A `g` regex carries `lastIndex` on the object itself, so a recursive call
// through one module-level `INLINE` would reset the position its own caller is mid-way
// through and drop the rest of the line. Found in the model's own summary: it wrote
// **`roman.py`** and the page showed the backticks, because `strong` took the text
// literally and never looked inside it.
function inline(text, into) {
  const scan = new RegExp(INLINE.source, "g");
  let at = 0, found;
  while ((found = scan.exec(text)) !== null) {
    if (found.index > at) into.appendChild(document.createTextNode(text.slice(at, found.index)));
    // Code is literal by definition and stays a leaf; emphasis is a wrapper and recurses.
    if (found[2] !== undefined) put(into, "code", found[2].trim());
    else if (found[3] !== undefined) inline(found[3], nest(into, "strong"));
    else if (found[4] !== undefined) inline(found[4], nest(into, "em"));
    else if (SAFE_LINK.test(found[6])) {
      const link = document.createElement("a");
      link.href = found[6];
      link.textContent = found[5] || found[6];
      link.target = "_blank"; link.rel = "noopener noreferrer";
      into.appendChild(link);
    } else {
      // A javascript: or data: URL is shown as the text it is, never made clickable.
      into.appendChild(document.createTextNode(found[0]));
    }
    at = scan.lastIndex;
  }
  if (at < text.length) into.appendChild(document.createTextNode(text.slice(at)));
}

function cells(line) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
}

function markdown(source) {
  const box = document.createElement("div");
  box.className = "md";
  const lines = String(source).replace(/\r\n?/g, "\n").split("\n");
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    const fence = line.match(/^\s*(```|~~~)/);
    if (fence) {
      const body = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
      i++;
      const pre = document.createElement("pre");
      put(pre, "code", body.join("\n"));
      box.appendChild(pre);
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const node = document.createElement("h" + heading[1].length);
      inline(heading[2].replace(/\s*#+\s*$/, ""), node);
      box.appendChild(node); i++; continue;
    }

    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      box.appendChild(document.createElement("hr")); i++; continue;
    }

    // A table is a row followed by a rule -- both, or it is only a paragraph with pipes.
    if (line.trim().startsWith("|") && i + 1 < lines.length
        && /^[\s|:-]*-[\s|:-]*$/.test(lines[i + 1]) && lines[i + 1].includes("|")) {
      const table = document.createElement("table");
      const head = document.createElement("tr");
      for (const cell of cells(line)) inline(cell, head.appendChild(document.createElement("th")));
      table.appendChild(head);
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const row = document.createElement("tr");
        for (const cell of cells(lines[i])) inline(cell, row.appendChild(document.createElement("td")));
        table.appendChild(row); i++;
      }
      box.appendChild(table);
      continue;
    }

    if (/^\s*>/.test(line)) {
      const quoted = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) quoted.push(lines[i++].replace(/^\s*>\s?/, ""));
      const block = absorb(document.createElement("blockquote"), markdown(quoted.join("\n")));
      box.appendChild(block);
      continue;
    }

    const bullet = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
    if (bullet) {
      const indent = bullet[1].length;
      const list = document.createElement(/\d/.test(bullet[2]) ? "ol" : "ul");
      while (i < lines.length) {
        const item = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
        if (!item || item[1].length < indent) break;
        if (item[1].length > indent && list.lastChild) {
          const start = i;
          while (i < lines.length && (lines[i].match(/^(\s*)/)[1].length > indent) && lines[i].trim()) i++;
          if (i === start) i++;      // never stall, whatever the indentation does
          absorb(list.lastChild, markdown(lines.slice(start, i).map((l) => l.slice(indent + 2)).join("\n")));
          continue;
        }
        const entry = document.createElement("li");
        inline(item[3], entry);
        list.appendChild(entry); i++;
      }
      box.appendChild(list);
      continue;
    }

    const paragraph = [];
    while (i < lines.length && lines[i].trim() && !BLOCK.test(lines[i])) paragraph.push(lines[i++]);
    if (!paragraph.length) { paragraph.push(lines[i++]); }   // a block shape nothing claimed
    const node = document.createElement("p");
    inline(paragraph.join("\n"), node);
    box.appendChild(node);
  }
  return box;
}

// ---------------------------------------------------------------------------- highlighting
// Hand-written and deliberately shallow, for the reason the markdown renderer is: no build
// step, no CDN, and the page should work with the network down.
//
// One pass with real string and comment *states*, not a pile of independent regexes -- a `#`
// inside a string literal is the bug that approach always has, and a `//` inside a Python
// file is floor division rather than a comment, which is why the comment markers come from
// the file's own language instead of being tried all at once.
//
// Shallow means shallow. It knows strings, comments, numbers and one shared keyword set; it
// does not know types, calls or scope. A keyword coloured wrongly here costs a colour, never
// a wrong answer, which is the only reason a heuristic is acceptable in this spot at all.
const SYNTAX = {
  py: { line: ["#"], block: null, triple: true },
  c: { line: ["//"], block: ["/*", "*/"], triple: false },
  sh: { line: ["#"], block: null, triple: false },
};
const FAMILY = {
  py: [".py", ".pyi"],
  c: [".go", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".swift", ".rs", ".c", ".h",
      ".cc", ".cpp", ".hpp", ".java", ".kt", ".cs", ".scala", ".php"],
  sh: [".sh", ".bash", ".zsh", ".fish"],
};
// One set across every language on purpose: telling them apart would need a real parser,
// and a Go `func` highlighted in a Python file is a cost nobody pays.
const KEYWORDS = new Set((
  "def class return if elif else for while in is not and or import from as pass raise try " +
  "except finally with lambda yield async await global nonlocal del assert break continue " +
  "func var let const type struct interface package go defer chan map range switch case " +
  "default fallthrough select goto new make nil true false none null self this super " +
  "public private protected static final extern inline void int string bool float double " +
  "enum protocol extension guard where throws throw catch do fn impl trait mut pub use " +
  "match unsafe export function typeof instanceof null undefined then fi esac done elif"
).split(" "));

function familyFor(call) {
  if (!["read_file", "write_file", "edit_file"].includes(call.name || "")) return "";
  const path = String((call.arguments || {}).path || "");
  const dot = path.lastIndexOf(".");
  if (dot < 0) return "";
  const suffix = path.slice(dot).toLowerCase();
  for (const [family, suffixes] of Object.entries(FAMILY)) {
    if (suffixes.includes(suffix)) return family;
  }
  return "";
}

function tokens(text, family) {
  const rules = SYNTAX[family];
  if (!rules) return [["plain", text]];
  const out = [];
  let plain = "";
  let at = 0;
  const flush = () => { if (plain) { out.push(["plain", plain]); plain = ""; } };

  while (at < text.length) {
    const rest = text.slice(at, at + 3);
    const line = rules.line.find((marker) => rest.startsWith(marker));
    if (line) {
      const ends = text.indexOf("\n", at);
      const stop = ends === -1 ? text.length : ends;
      flush(); out.push(["com", text.slice(at, stop)]); at = stop; continue;
    }
    if (rules.block && rest.startsWith(rules.block[0])) {
      const ends = text.indexOf(rules.block[1], at + rules.block[0].length);
      const stop = ends === -1 ? text.length : ends + rules.block[1].length;
      flush(); out.push(["com", text.slice(at, stop)]); at = stop; continue;
    }
    const char = text[at];
    if (char === '"' || char === "'" || char === "`") {
      const fence = rules.triple && text.startsWith(char.repeat(3), at) ? char.repeat(3) : char;
      let end = at + fence.length;
      while (end < text.length) {
        if (text[end] === "\\") { end += 2; continue; }
        if (text.startsWith(fence, end)) { end += fence.length; break; }
        // An unterminated single-quoted string ends at the newline rather than eating the
        // rest of the file -- an apostrophe in a comment would otherwise colour everything
        // after it.
        if (fence.length === 1 && text[end] === "\n") break;
        end++;
      }
      flush(); out.push(["str", text.slice(at, Math.min(end, text.length))]);
      at = Math.min(end, text.length); continue;
    }
    if (/[A-Za-z_]/.test(char)) {
      let end = at;
      while (end < text.length && /[A-Za-z0-9_]/.test(text[end])) end++;
      const word = text.slice(at, end);
      if (KEYWORDS.has(word)) { flush(); out.push(["key", word]); } else { plain += word; }
      at = end; continue;
    }
    if (/[0-9]/.test(char)) {
      let end = at;
      while (end < text.length && /[0-9a-fA-FxXoO_.]/.test(text[end])) end++;
      flush(); out.push(["num", text.slice(at, end)]); at = end; continue;
    }
    plain += char; at++;
  }
  flush();
  return out;
}

function paint(into, pieces) {
  into.textContent = "";
  for (const [kind, chunk] of pieces) {
    if (kind === "plain") { into.appendChild(document.createTextNode(chunk)); continue; }
    const span = document.createElement("span");
    span.className = `hl-${kind}`;
    span.textContent = chunk;
    into.appendChild(span);
  }
}

const JSON_TOKENS = /("(?:\\.|[^"\\])*")(\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?/g;

function paintJson(into, value) {
  const text = JSON.stringify(value ?? {}, null, 2);
  const pieces = [];
  let at = 0, found;
  JSON_TOKENS.lastIndex = 0;
  while ((found = JSON_TOKENS.exec(text)) !== null) {
    if (found.index > at) pieces.push(["plain", text.slice(at, found.index)]);
    if (found[1] && found[2]) {
      pieces.push(["key", found[1]], ["plain", found[2]]);   // a key, not a value
    } else if (found[1]) {
      pieces.push(["str", found[1]]);
    } else {
      pieces.push(["num", found[0]]);
    }
    at = JSON_TOKENS.lastIndex;
  }
  if (at < text.length) pieces.push(["plain", text.slice(at)]);
  paint(into, pieces);
}

function showTool(row) {
  const call = row._call || {};
  $("dname").textContent = call.name || "tool";
  const result = row._result;
  $("dmark").textContent = result === undefined
    ? "still running"
    : `${row._bad ? "failed" : "ok"} · ${result.length.toLocaleString()} chars`;
  paintJson($("dargs"), call.arguments);
  // Only a file's contents are coloured. A shell log or a box tree is not source in any
  // language, and guessing at one would colour half a `pytest` run for no reason.
  const family = familyFor(call);
  if (result === undefined) {
    $("dresult").textContent = "(no result yet — this turn has not finished)";
  } else if (family && result) {
    paint($("dresult"), tokens(result, family));
  } else {
    $("dresult").textContent = result || "(empty)";
  }
  $("detail").classList.add("on");
  $("shade").classList.add("on");
}

function weigh(m) {
  let n = (m.content || "").length;
  for (const call of m.tool_calls || []) {
    n += (call.name || "").length + JSON.stringify(call.arguments || {}).length;
  }
  return n;
}

// Mirrors compaction.view: the system message, the summary standing in for everything behind
// the boundary, then the kept tail and everything after it.
function contextChars() {
  let boundary = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "compaction") { boundary = i; break; }
  }
  if (boundary < 0) return messages.reduce((n, m) => n + weigh(m), 0);
  let previous = -1;
  for (let i = boundary - 1; i >= 0; i--) {
    if (messages[i].role === "compaction") { previous = i; break; }
  }
  let start = boundary, kept = 0;
  for (let i = boundary - 1; i > Math.max(previous, 0); i--) {
    if (messages[i].role === "assistant") { start = i; if (++kept >= KEEP_TURNS) break; }
  }
  let total = weigh(messages[0] || {}) + (messages[boundary].content || "").length;
  for (let i = start; i < boundary; i++) total += weigh(messages[i]);
  for (let i = boundary + 1; i < messages.length; i++) total += weigh(messages[i]);
  return total;
}

function paintContext() {
  const box = $("ctx");
  if (!messages.length) { box.textContent = ""; return; }
  const tokens = Math.round(contextChars() / CHARS_PER_TOKEN);
  const share = ctxWindow ? tokens / ctxWindow : 0;
  box.textContent = `≈ ${tokens.toLocaleString()} / ${brief(ctxWindow)} tokens · ${Math.round(share * 100)}%`;
  box.className = share >= compactAt ? "over" : share >= compactAt - 0.15 ? "near" : "";
}


// The on-screen keyboard, which is the one thing that moves a page nothing can scroll.
//
// `interactive-widget=resizes-content` covers the browsers that honour it: they shrink the
// layout viewport, `offsetTop` stays 0, and the second line below does nothing. iOS ignores
// it. There the layout viewport keeps its full height and Safari *pans* the visual one down
// to reveal the focused field -- so a `position: fixed` body pinned to `top: 0` stays with
// the layout viewport, which has just scrolled off the top of the screen. The interface does
// not shrink or move; it goes somewhere you cannot look at. Reported from a phone: tapping
// the composer made the whole page vanish.
//
// So follow both numbers. `height` sizes the page to what is visible; `offsetTop` puts it
// where the visible part now is. `scroll` matters as much as `resize`, because panning
// changes the offset without changing the height.
if (window.visualViewport) {
  const view = window.visualViewport;
  const fit = () => {
    document.documentElement.style.setProperty("--screen", `${view.height}px`);
    document.body.style.top = `${view.offsetTop}px`;
  };
  view.addEventListener("resize", fit);
  view.addEventListener("scroll", fit);
  fit();
}

// ---------------------------------------------------------------------------- the glider
// Conway's rules, actually run, on a 5x5 torus seeded with a glider. Five cells throughout
// and period 20, so it wraps forever without decaying -- checked by simulation rather than
// assumed, because on a 4x4 torus the glider collides with its own wrap and collapses into
// an eight-cell blob by the second generation. 5x5 is the smallest board that keeps it a
// glider, which is why the icon is that size and not smaller.
const LIFE = 5;
const board = $("glider");
let colony = [[0, 1], [1, 2], [2, 0], [2, 1], [2, 2]];
const dots = [];
if (board) for (let r = 0; r < LIFE; r++) {
  for (let c = 0; c < LIFE; c++) {
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    dot.setAttribute("x", c * 3);
    dot.setAttribute("y", r * 3);
    dot.setAttribute("width", 2);
    dot.setAttribute("height", 2);
    dot.setAttribute("fill-opacity", 0);
    board.appendChild(dot);
    dots.push(dot);
  }
}

function generation() {
  const alive = new Set(colony.map(([r, c]) => r * LIFE + c));
  dots.forEach((dot, at) => dot.setAttribute("fill-opacity", alive.has(at) ? 1 : 0.12));
  const neighbours = new Map();
  for (const [r, c] of colony) {
    for (let dr = -1; dr <= 1; dr++) {
      for (let dc = -1; dc <= 1; dc++) {
        if (!dr && !dc) continue;
        const at = ((r + dr + LIFE) % LIFE) * LIFE + ((c + dc + LIFE) % LIFE);
        neighbours.set(at, (neighbours.get(at) || 0) + 1);
      }
    }
  }
  colony = [];
  for (const [at, count] of neighbours) {
    if (count === 3 || (count === 2 && alive.has(at))) {
      colony.push([Math.floor(at / LIFE), at % LIFE]);
    }
  }
}

if (board) generation();   // drawn once regardless, so it is never an empty square
if (board && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
  setInterval(generation, 420);
}
