// §6.4 — the JS test floor. Unit tests for mockup/lib.js's pure helpers, run by
// `node --test tests/js/` in CI (ci.yml). These are the formatting / escaping /
// CSV-building functions that §1.6's frontend bugs hid in; a tiny harness here is
// the structural fix for "6,200 lines of JS, zero automated coverage."
const { test } = require("node:test");
const assert = require("node:assert");
const lib = require("../../mockup/lib.js");

test("fmtMoney — compact tiers + null/NaN", () => {
  assert.equal(lib.fmtMoney(1_500_000), "$1.50M");
  assert.equal(lib.fmtMoney(12_000_000), "$12.0M");   // >=10M → 1 decimal
  assert.equal(lib.fmtMoney(12_000), "$12K");
  assert.equal(lib.fmtMoney(1_500), "$1.5K");
  assert.equal(lib.fmtMoney(940), "$940");
  assert.equal(lib.fmtMoney(null), "—");
  assert.equal(lib.fmtMoney(NaN), "—");
});

test("fmtMoneyExact + fmtInt", () => {
  assert.equal(lib.fmtMoneyExact(1234567), "$1,234,567");
  assert.equal(lib.fmtMoneyExact(0), "$0");
  assert.equal(lib.fmtInt(4176), "4,176");
  assert.equal(lib.fmtInt(null), "0");
});

test("escapeHtml — all five entities + null", () => {
  assert.equal(lib.escapeHtml(`<a href="x">Tom & 'Jerry'</a>`),
    "&lt;a href=&quot;x&quot;&gt;Tom &amp; &#39;Jerry&#39;&lt;/a&gt;");
  assert.equal(lib.escapeHtml(null), "");
  assert.equal(lib.escapeAttr("&"), "&amp;");
});

test("csvEscape — §3.8 formula-injection guard + quoting", () => {
  // leading = + - @ tab/CR are neutralized with a leading '
  for (const s of ["=HYPERLINK(1)", "+1", "-2000", "@cmd", "\tx", "\rx"]) {
    assert.equal(lib.csvEscape(s)[0], "'", `expected quote-prefix for ${JSON.stringify(s)}`);
  }
  // comma / quote / newline → wrapped in quotes with doubled quotes
  assert.equal(lib.csvEscape('a,b'), '"a,b"');
  assert.equal(lib.csvEscape('he said "hi"'), '"he said ""hi"""');
  assert.equal(lib.csvEscape("line1\nline2"), '"line1\nline2"');
  // safe value untouched; null → empty
  assert.equal(lib.csvEscape("ActBlue"), "ActBlue");
  assert.equal(lib.csvEscape(null), "");
});

test("buildCSV — header + escaped rows, both amount columns", () => {
  const cols = ["donor_name", "amount", "amount_2026"];
  const rows = [
    { donor_name: "Steven A. Cohen", amount: 5000, amount_2026: 7562.28 },
    { donor_name: "=EVIL()", amount: -100, amount_2026: null },
  ];
  const csv = lib.buildCSV(rows, cols);
  const lines = csv.split("\n");
  assert.equal(lines[0], "donor_name,amount,amount_2026");
  assert.equal(lines[1], "Steven A. Cohen,5000,7562.28");
  assert.equal(lines[2], "'=EVIL(),'-100,");   // formula guard + null → empty
});

test("partyTiltLabel — leaning buckets, mixed, and no-giving", () => {
  // Clear DEM lean (≥60%).
  assert.equal(lib.partyTiltLabel(80, 20), "Democratic-leaning (80% DEM)");
  // Clear REP lean (each side rounded independently).
  assert.equal(lib.partyTiltLabel(20, 80), "Republican-leaning (80% REP)");
  // Mixed band (40–60%).
  assert.equal(lib.partyTiltLabel(50, 50), "Mixed (50% DEM / 50% REP)");
  assert.equal(lib.partyTiltLabel(55, 45), "Mixed (55% DEM / 45% REP)");
  // Independent rounding: 1 vs 2 → 33% / 67%; 67≥60 so it's a REP lean.
  assert.equal(lib.partyTiltLabel(1, 2), "Republican-leaning (67% REP)");
  // No two-party dollars → neutral label (OTH-only or zero).
  assert.equal(lib.partyTiltLabel(0, 0), "No party-classified giving");
  assert.equal(lib.partyTiltLabel(null, null), "No party-classified giving");
});
