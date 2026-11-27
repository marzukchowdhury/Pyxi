/**
 * Connecting an account happens on the other site's page, in the browser. When that page says
 * no (Google's "Access blocked" for an account it does not let in yet, a tab closed, a sign-in
 * given up), nothing comes back to Pyxi at all. So after a while Pyxi says so itself, and the
 * button can be pressed again: it used to read "Check your browser" for good.
 */
export const STUCK_MS = 25_000;
export const STUCK_WORDS =
  "Still waiting for Google's page. If it says access is blocked, Google hasn't opened Pyxi's connection to this account yet: that is on Pyxi's side, not yours. Everything else in Pyxi works without it, and you can try again later.";
