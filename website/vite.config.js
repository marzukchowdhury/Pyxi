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

// The site is on sale: it must say who to write to, and every address on it comes from siteUrl.
// A build with either missing stops here, loudly, instead of printing "[contact email]" on the
// terms and the privacy policy or pointing search engines at the wrong host.
if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(config.contactEmail ?? "")) {
  throw new Error('site.config.json: "contactEmail" is empty or not an address. The footer, the terms and the privacy policy all print it.');
}
if (!/^https:\/\/[^/\s]+$/.test(config.siteUrl ?? "")) {
  throw new Error('site.config.json: "siteUrl" must be the site\'s https address with no path and no trailing slash.');
}

// The pages people can land on, by their clean address (Vercel serves /pricing, not /pricing.html).
// The sitemap lists exactly these. Left out on purpose: the 404, the two pages Stripe sends the
// browser back to, and the 3D how-to that only plays inside the home page.
const PAGES = ["index", "download", "features", "ask", "team", "why", "faq", "pricing", "privacy", "terms"];

function sitemap() {
  const day = new Date().toISOString().slice(0, 10);
  const urls = PAGES.map((p) => `${config.siteUrl}/${p === "index" ? "" : p}`);
  return (
    '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
    urls.map((u) => `  <url><loc>${u}</loc><lastmod>${day}</lastmod></url>`).join("\n") +
    "\n</urlset>\n"
  );
}

/** robots.txt is a plain file in public/: it must name the sitemap at the address above. */
function checkRobots() {
  const robots = readFileSync(resolve(root, "public/robots.txt"), "utf8");
  const want = `Sitemap: ${config.siteUrl}/sitemap.xml`;
  if (!robots.split(/\r?\n/).includes(want)) throw new Error(`public/robots.txt needs the line "${want}"`);
}

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
  md = md.replaceAll("**[contact email]**", `[${config.contactEmail}](mailto:${config.contactEmail})`);
  if (/\[?contact email\]?/i.test(md)) {
    // Written some other way ("[contact email]" without the bold): it would be printed as it is.
    throw new Error(`docs/${name}.md still has a "contact email" placeholder the build can't fill. Write it as **[contact email]**.`);
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
    buildStart() {
      checkRobots();
    },
    generateBundle() {
      // /sitemap.xml, made from the real pages at every build.
      this.emitFile({ type: "asset", fileName: "sitemap.xml", source: sitemap() });
    },
  };
}

export default defineConfig({
  plugins: [site()],
  build: {
    target: "es2020",
    rollupOptions: {
      input: {
        ...Object.fromEntries(PAGES.map((p) => [p, resolve(root, `${p}.html`)])),
        404: resolve(root, "404.html"),
        // where Stripe Checkout sends the browser back to (the server's success and cancel URLs)
        welcome: resolve(root, "welcome.html"),
        "billing-cancelled": resolve(root, "billing-cancelled.html"),
        // the RAM how-to, played inside the home page's 3D section
        ram: resolve(root, "film2/ram.html"),
      },
    },
  },
});
