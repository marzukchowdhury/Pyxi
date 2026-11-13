import { copyFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { defineConfig } from "vite";
import { marked } from "marked";

const root = import.meta.dirname;
const config = JSON.parse(readFileSync(resolve(root, "site.config.json"), "utf8"));
// The Mac line has three states, and the templates can't nest an if, so these are worked out here:
// a Mac download when there's one, else the waitlist when there's one, else "next on the roadmap".
config.macWaitlistOnly = !config.macDownloadUrl && config.macWaitlistUrl ? "yes" : "";
config.macNotYet = !config.macDownloadUrl && !config.macWaitlistUrl ? "yes" : "";

/**
 * The privacy and terms pages are docs/PRIVACY.md and docs/TERMS.md, rendered at build time so
 * the site never drifts from them. Vercel only uploads website/, so every local build refreshes
 * the copies in content/ (committed) and the cloud build reads those.
 */
function docHtml(name) {
  const source = resolve(root, `../docs/${name}.md`);
  const copy = resolve(root, `content/${name}.md`);
  if (existsSync(source)) {
    mkdirSync(dirname(copy), { recursive: true });
    copyFileSync(source, copy);
  }
  let md = readFileSync(copy, "utf8");
  md = md.replace(/^# .*\n/, ""); // the page supplies its own title
  if (config.contactEmail) {
    md = md.replaceAll("**[contact email]**", `[${config.contactEmail}](mailto:${config.contactEmail})`);
  }
  const html = marked.parse(md, { gfm: true });
  // Unfilled placeholders such as [contact email] stand out instead of reading as finished copy.
  return html.replace(/<strong>\[([^\]<]+)\]<\/strong>/g, '<mark class="todo">$1</mark>');
}

/**
 * Tiny templating for the HTML pages:
 *   <!--include:nav-->                   partials/nav.html
 *   {{downloadUrl}}                     a value from site.config.json
 *   <!--if:key--> a <!--else--> b <!--end-->   keep a when the value is set, else b
 *   <!--doc:PRIVACY--> <!--doc:TERMS-->    the rendered policy and terms
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
      html = html.replace(/<!--doc:(PRIVACY|TERMS)-->/g, (_, name) => docHtml(name));
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
        features: resolve(root, "features.html"),
        ask: resolve(root, "ask.html"),
        team: resolve(root, "team.html"),
        why: resolve(root, "why.html"),
        faq: resolve(root, "faq.html"),
        privacy: resolve(root, "privacy.html"),
        terms: resolve(root, "terms.html"),
        404: resolve(root, "404.html"),
        // the RAM how-to, played inside the home page's 3D section
        ram: resolve(root, "film2/ram.html"),
      },
    },
  },
});
