// The renderer pages carry a strict Content-Security-Policy (see overlay.html, panel.html). Vite's
// dev server needs a little more for hot reload: its inline preamble script and a socket on
// localhost. Only while serving; built pages keep the strict policy.
import type { Plugin } from "vite";

export function devCsp(): Plugin {
  return {
    name: "pyxi-dev-csp",
    apply: "serve",
    transformIndexHtml: (html) =>
      html
        .replace("script-src 'self'", "script-src 'self' 'unsafe-inline'")
        .replace("connect-src 'self'", "connect-src 'self' http://localhost:* ws://localhost:*"),
  };
}
