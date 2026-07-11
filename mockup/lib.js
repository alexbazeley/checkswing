// mockup/lib.js — pure, testable helpers shared by the SPA (loaded as a plain
// <script> before index.html's main script, attaching them as globals) and by
// the Node test harness (§6.4 — tests/js/lib.test.mjs, `node --test`). These are
// the functions §1.6's bugs hid in: formatting, HTML/CSV escaping, CSV building.
// NO DOM, no app state — keep it that way so it stays unit-testable in Node.
(function (root) {
  "use strict";

  // Compact money: $1.2M / $12K / $1.2K / $940. null/NaN → em dash.
  const fmtMoney = (n) => {
    if (n == null || isNaN(n)) return "—";
    const a = Math.abs(n);
    if (a >= 1_000_000) return "$" + (n / 1_000_000).toFixed(n >= 10_000_000 ? 1 : 2) + "M";
    if (a >= 10_000)    return "$" + Math.round(n / 1000) + "K";
    if (a >= 1000)      return "$" + (n / 1000).toFixed(1) + "K";
    return "$" + Math.round(n).toLocaleString();
  };
  const fmtMoneyExact = (n) => "$" + Math.round(n).toLocaleString();
  const fmtInt = (n) => (n ?? 0).toLocaleString();

  const escapeHtml = (s) => {
    if (s == null) return "";
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  };
  const escapeAttr = escapeHtml;

  // CSV cell escaping with spreadsheet-formula-injection neutralization (§3.8):
  // a leading = + - @ (or tab/CR) makes Excel/Sheets evaluate a donor-filed cell
  // (e.g. employer "=HYPERLINK(...)"), so prefix with ' to force text.
  const csvEscape = (v) => {
    if (v == null) return "";
    let s = String(v);
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
    if (s.includes('"') || s.includes(",") || s.includes("\n")) {
      return '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
  };

  // Pure CSV assembly (header + escaped rows); the DOM blob/download stays in the SPA.
  const buildCSV = (rows, cols) => {
    const lines = [cols.join(",")];
    for (const r of rows) lines.push(cols.map((c) => csvEscape(r[c])).join(","));
    return lines.join("\n");
  };

  const api = { fmtMoney, fmtMoneyExact, fmtInt, escapeHtml, escapeAttr, csvEscape, buildCSV };
  if (typeof module !== "undefined" && module.exports) module.exports = api;  // Node
  else Object.assign(root, api);                                              // browser globals
})(typeof globalThis !== "undefined" ? globalThis : this);
