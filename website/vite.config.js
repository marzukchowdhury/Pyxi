import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig } from "vite";
import { marked } from "marked";

const root = import.meta.dirname;
const config = JSON.parse(readFileSync(resolve(root, "site.config.json"), "utf8"));

/** The privacy page is docs/PRIVACY.md, rendered at build time so the site never drifts from it. */
function privacyHtml() {
  let md = readFileSync(resolve(root, "../docs/PRIVACY.md"), "utf8");
  md = md.replace(/^# .*\n/, ""); // the page supplies its own title
  if (config.contactEmail) {
    md = md.replaceAll("**[privacy contact email]**", `[${config.contactEmail}](mailto:${config.contactEmail})`);
  }
  const html = marked.parse(md, { gfm: true });
  // Unfilled placeholders such as [privacy contact email] stand out instead of reading as finished copy.
  return html.replace(/<strong>\[([^\]<]+)\]<\/strong>/g, '<mark class="todo">$1</mark>');
}

/**
 * Tiny templating for the HTML pages:
 *   <!--include:nav-->                   partials/nav.html
 *   {{downloadUrl}}                     a value from site.config.json
 *   <!--if:key--> a <!--else--> b <!--end-->   keep a when the value is set, else b
 *   <!--privacy-->                       the rendered privacy policy
 */
function site() {
  return {
    name: "pyxi-site",
    transformIndexHtml(html) {
      html = html.replace(/<!--include:([\w-]+)-->/g, (_, name) =>
        readFileSync(resolve(root, "partials", `${name}.html`), "utf8"),
      );
      html = html.replace(/<!--if:(\w+)-->([\s\S]*?)(?:<!--else-->([\s\S]*?))?<!--end-->/g, (_, key, yes, no = "") =>
        config[key] ? yes : no,
      );
      html = html.replace("<!--privacy-->", privacyHtml);
      return html.replace(/\{\{(\w+)\}\}/g, (m, key) => {
        if (!(key in config)) throw new Error(`site.config.json has no "${key}"`);
        return String(config[key]);
      });
    },
  };
}

export default defineConfig({
  plugins: [site()],
  build: {
    target: "es2020",
    rollupOptions: {
      input: {
        index: resolve(root, "index.html"),
        download: resolve(root, "download.html"),
        privacy: resolve(root, "privacy.html"),
        404: resolve(root, "404.html"),
      },
    },
  },
});
