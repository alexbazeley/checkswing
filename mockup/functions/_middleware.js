/**
 * CheckSwing — site-wide password gate (Cloudflare Pages Functions middleware).
 *
 * Runs at the edge on EVERY request — the HTML, data.json, state_data.json, the
 * per-committee beneficiary chunks, and every asset — before anything is served.
 * Because it sits in front of the static files, the data can't be fetched around
 * the gate (a client-side prompt could not do this).
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * HOW TO TURN IT ON
 *   Cloudflare dashboard → your Pages project → Settings → Variables and Secrets.
 *   Add (to the Production environment — and Preview too if you want previews
 *   gated):
 *     • SITE_PASSWORD  — the shared password   (add as an encrypted Secret)
 *     • SITE_USER      — optional username      (defaults to "checkswing")
 *   Variable changes take effect on the next deployment (push, or "Retry
 *   deployment" on the latest one).
 *
 * The gate is INACTIVE until SITE_PASSWORD is set, so deploying this file does
 * not lock anyone out before you've configured the secret. Remove the secret to
 * make the site public again.
 *
 * Note: while gated, social-share crawlers can't fetch the OG image, so link
 * previews won't render — expected for a private site.
 * ─────────────────────────────────────────────────────────────────────────────
 */

const REALM = "CheckSwing — private";

function unauthorized() {
  return new Response("Authentication required.", {
    status: 401,
    headers: {
      "WWW-Authenticate": `Basic realm="${REALM}", charset="UTF-8"`,
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

/* Length-independent, non-short-circuiting compare so neither the username nor
   the password leaks via response timing. */
function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) {
    return false;
  }
  let mismatch = 0;
  for (let i = 0; i < a.length; i++) {
    mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return mismatch === 0;
}

export const onRequest = async ({ request, env, next }) => {
  const expectedPass = env.SITE_PASSWORD;

  // Gate stays off until a password is configured (see header comment).
  if (!expectedPass) return next();

  const expectedUser = env.SITE_USER || "checkswing";
  const header = request.headers.get("Authorization") || "";

  if (header.startsWith("Basic ")) {
    let decoded = "";
    try {
      decoded = atob(header.slice(6));
    } catch {
      return unauthorized();
    }
    const sep = decoded.indexOf(":");
    const user = sep >= 0 ? decoded.slice(0, sep) : decoded;
    const pass = sep >= 0 ? decoded.slice(sep + 1) : "";

    // Evaluate both comparisons every time (no &&) so a wrong username can't
    // short-circuit the password check.
    const userOk = safeEqual(user, expectedUser);
    const passOk = safeEqual(pass, expectedPass);
    if (userOk && passOk) return next();
  }

  return unauthorized();
};
