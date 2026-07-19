// The SPA is a single 9k-line file whose entire application lives in one inline
// <script>. A syntax error anywhere in it is not a partial failure — the whole
// script fails to parse, every route renders "Loading data…" forever, and
// nothing in the existing test suite notices, because the suite only imports
// lib.js. That happened once during the GROUNDRULES pass (a botched string
// replacement left a dangling backtick in renderStateRecipient), and the only
// thing that caught it was a headless browser noticing the page was empty.
//
// This is the cheap structural fix: extract the inline scripts and hand them to
// the JS parser. It runs in CI via `node --test tests/js/*.test.js`.
const { test } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const INDEX = path.join(__dirname, "..", "..", "mockup", "index.html");

function inlineScripts(html) {
  // Strip HTML comments first — index.html documents its own <script src>
  // loading order inside a comment, which otherwise reads as a script tag.
  const stripped = html.replace(/<!--[\s\S]*?-->/g, "");
  // Only <script> elements with no src= attribute carry inline code.
  const out = [];
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = re.exec(stripped)) !== null) out.push(m[1]);
  return out;
}

test("mockup/index.html inline scripts parse as JavaScript", () => {
  const html = fs.readFileSync(INDEX, "utf8");
  const scripts = inlineScripts(html);

  assert.ok(scripts.length > 0, "expected at least one inline <script> in index.html");

  scripts.forEach((src, i) => {
    if (!src.trim()) return;
    assert.doesNotThrow(
      // `new vm.Script` compiles without executing — a parse check, not a run.
      () => new vm.Script(src, { filename: `index.html:inline-script-${i}` }),
      `inline <script> #${i} in mockup/index.html has a syntax error`
    );
  });
});

test("the app script defines the route renderers it dispatches to", () => {
  const html = fs.readFileSync(INDEX, "utf8");
  const app = inlineScripts(html).join("\n");

  // A dangling template literal typically swallows following declarations, so a
  // spot-check that the render functions still exist catches truncation that is
  // technically parseable.
  const required = [
    "renderHome", "renderLeague", "renderOwner", "renderStates",
    "renderStateRecipient", "renderTeam", "renderMethodology",
    "notFoundState", "emptyState", "filingCoordinate",
  ];
  for (const fn of required) {
    assert.match(app, new RegExp(`function\\s+${fn}\\b`), `missing function ${fn}()`);
  }
});
