// Runs the watch page's markdown renderer outside a browser, so `test_pages.py` can assert
// on what it produces.
//
// The renderer is hand-written and lives in a file that is inlined into a page at request
// time -- there is no module boundary and no build step, which is deliberate (see
// `server.page`) and which also means nothing in the Python suite can reach it. So this
// slices the renderer out of `shared.js` between its own section markers, evaluates it
// against a DOM stub that implements only the handful of calls it makes, and prints the
// result as a small HTML-ish string.
//
// The stub is not a DOM. It exists to prove the renderer builds the right *shape*, which is
// the thing that broke: `strong` was a leaf, so anything nested inside bold came out as
// literal source.
//
//   node tests/render.mjs <path-to-shared.js> '<markdown>'

import { readFileSync } from "node:fs";

class Node {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.className = "";
    this.style = {};
    this.dataset = {};
    this.classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  }
  appendChild(child) { this.children.push(child); return child; }
  set textContent(text) { this.children = [{ tag: "#text", text: String(text) }]; }
  get textContent() { return ""; }
  get firstChild() { return this.children[0]; }
  get lastChild() { return this.children[this.children.length - 1]; }
  removeChild(child) {
    this.children = this.children.filter((each) => each !== child);
    return child;
  }
  addEventListener() {}
  setAttribute() {}
  querySelector() { return new Node("div"); }
  querySelectorAll() { return []; }
}

globalThis.document = {
  createElement: (tag) => new Node(tag),
  createElementNS: (_ns, tag) => new Node(tag),
  createTextNode: (text) => ({ tag: "#text", text: String(text) }),
  getElementById: () => new Node("div"),
};
globalThis.window = globalThis;

// Between the markers, so this keeps working when lines move. Stopping before "the glider"
// leaves out everything that touches a live page at load time.
function renderer(source) {
  const lines = source.split("\n");
  const marker = (word) =>
    lines.findIndex((line) => line.startsWith("// ---") && line.includes(word));
  const from = marker("markdown");
  const to = marker("glider");
  if (from < 0 || to < 0) throw new Error("cannot find the renderer's section markers");
  return lines.slice(from, to).join("\n");
}

const flat = (node) => {
  if (node.tag === "#text") return node.text;
  const inside = (node.children || []).map(flat).join("");
  return node.tag === "div" ? inside : `<${node.tag}>${inside}</${node.tag}>`;
};

eval(renderer(readFileSync(process.argv[2], "utf8")) + "\nglobalThis.md = markdown;");
process.stdout.write(flat(globalThis.md(process.argv[3])));
