// Verifies the static export after `next build`: SEO artifacts, placeholder leakage,
// sitemap/robots consistency, JSON-LD parseability and Open Graph images.
import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync, existsSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';

const OUT = resolve('out');
const SITE_URL = (process.env.NEXT_PUBLIC_SITE_URL || 'http://localhost:3000').replace(/\/+$/, '');
const ALLOW_PLACEHOLDER = process.env.SITE_ALLOW_PLACEHOLDER === '1';
// Only the build-time placeholder origin is leakage; docs legitimately reference
// 127.0.0.1:8878 for local runs and redacted example domains for test targets.
const PLACEHOLDER_RE = /https?:\/\/(localhost|127\.0\.0\.1|0\.0\.0\.0):3000\b/i;

const failures = [];
function fail(message) {
  failures.push(message);
}

function readHtml(urlPath) {
  const candidates = [join(OUT, urlPath, 'index.html'), join(OUT, `${urlPath}.html`)];
  for (const candidate of candidates) if (existsSync(candidate)) return { file: candidate, html: readFileSync(candidate, 'utf8') };
  return null;
}

function meta(html, attr, name) {
  const re = new RegExp(`<meta[^>]*\\b${attr}="${name}"[^>]*\\bcontent="([^"]*)"`, 'i');
  return (html.match(re) || [])[1] || '';
}

function linkHref(html, rel) {
  const re = new RegExp(`<link[^>]*\\brel="${rel}"[^>]*\\bhref="([^"]*)"`, 'i');
  return (html.match(re) || [])[1] || '';
}

function alternateHrefs(html) {
  return [...html.matchAll(/<link[^>]*\brel="alternate"[^>]*\bhrefLang="([^"]+)"[^>]*\bhref="([^"]+)"/gi)].map((m) => ({
    lang: m[1],
    href: m[2],
  }));
}

function walk(dir, predicate, files = []) {
  if (!existsSync(dir)) return files;
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, predicate, files);
    else if (predicate(p)) files.push(p);
  }
  return files;
}

// --- sitemap drives the indexable surface ---------------------------------
const sitemapPath = join(OUT, 'sitemap.xml');
const indexable = [];
if (!existsSync(sitemapPath)) {
  fail('missing out/sitemap.xml');
} else {
  const xml = readFileSync(sitemapPath, 'utf8');
  const locs = [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
  if (locs.length !== 12) fail(`sitemap should list 12 indexable URLs, found ${locs.length}`);
  for (const loc of locs) {
    if (!loc.startsWith(`${SITE_URL}/`)) fail(`sitemap URL does not use the configured origin: ${loc}`);
    const pathname = new URL(loc).pathname;
    indexable.push(pathname);
    const page = readHtml(pathname.slice(0, -1));
    if (!page) {
      fail(`sitemap URL has no exported page: ${pathname}`);
      continue;
    }
    if (linkHref(page.html, 'canonical') !== loc) fail(`${pathname}: canonical does not match sitemap (${linkHref(page.html, 'canonical')})`);
    const alts = alternateHrefs(page.html);
    if (alts.length !== 3) fail(`${pathname}: expected 3 hreflang alternates, found ${alts.length}`);
    const byLang = Object.fromEntries(alts.map((a) => [a.lang, a.href]));
    for (const lang of ['zh-CN', 'en', 'x-default']) {
      if (!byLang[lang]) fail(`${pathname}: missing hreflang ${lang}`);
    }
    if (byLang['zh-CN'] === byLang.en) fail(`${pathname}: zh-CN and en alternates are identical`);
    const ogUrl = meta(page.html, 'property', 'og:url');
    if (ogUrl !== loc) fail(`${pathname}: og:url ${ogUrl || '(missing)'} does not match ${loc}`);
    const ogImage = meta(page.html, 'property', 'og:image');
    if (!ogImage) fail(`${pathname}: missing og:image`);
    else if (!/^\/og\/(zh|en)\.png$/.test(new URL(ogImage).pathname)) fail(`${pathname}: og:image is not a stable /og/*.png path (${ogImage})`);
    if (!meta(page.html, 'property', 'og:title')) fail(`${pathname}: missing og:title`);
    if (!meta(page.html, 'property', 'og:description')) fail(`${pathname}: missing og:description`);
    if (meta(page.html, 'name', 'twitter:card') !== 'summary_large_image') fail(`${pathname}: twitter:card is not summary_large_image`);

    const ldJson = [...page.html.matchAll(/<script[^>]*type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/gi)];
    if (ldJson.length === 0) fail(`${pathname}: no JSON-LD block`);
    for (const block of ldJson) {
      try {
        const parsed = JSON.parse(block[1]);
        if (!parsed['@type'] && !parsed['@graph']) fail(`${pathname}: JSON-LD block has no @type/@graph`);
      } catch (error) {
        fail(`${pathname}: JSON-LD does not parse (${error.message})`);
      }
    }
  }
  if (locs.some((loc) => /\/styleguide\/?$/.test(new URL(loc).pathname))) fail('sitemap must not include /styleguide/');
  if (!xml.includes('<lastmod>')) fail('sitemap has no lastmod values');
}

// --- sitemap files must exist and be non-empty ----------------------------
if (!existsSync(join(OUT, '404.html'))) fail('missing out/404.html');
for (const file of ['out/404.html', 'out/404/index.html', 'out/_not-found/index.html']) {
  const path = join(process.cwd(), file);
  if (!existsSync(path)) continue;
  const html = readFileSync(path, 'utf8');
  if (!/name="robots"[^>]*content="noindex"/i.test(html)) fail(`${file}: 404 page is not noindex`);
}

// --- styleguide is not indexable and has no canonical ---------------------
for (const path of ['/styleguide/', '/en/styleguide/']) {
  const page = readHtml(path);
  if (!page) {
    fail(`missing exported page: ${path}`);
    continue;
  }
  if (!/name="robots"[^>]*content="noindex/i.test(page.html)) fail(`${path}: missing noindex robots meta`);
  if (linkHref(page.html, 'canonical')) fail(`${path}: styleguide must not declare a canonical URL`);
}

// --- robots.txt -----------------------------------------------------------
const robotsPath = join(OUT, 'robots.txt');
if (!existsSync(robotsPath)) {
  fail('missing out/robots.txt');
} else {
  const robots = readFileSync(robotsPath, 'utf8');
  if (!robots.includes(`Sitemap: ${SITE_URL}/sitemap.xml`)) fail(`robots.txt does not declare Sitemap: ${SITE_URL}/sitemap.xml`);
  if (!/Disallow:\s*\/styleguide\//i.test(robots)) fail('robots.txt does not disallow /styleguide/');
}

// --- Open Graph images: real PNG, 1200x630 --------------------------------
for (const name of ['/og/zh.png', '/og/en.png']) {
  const file = join(OUT, name);
  if (!existsSync(file)) {
    fail(`missing ${name} (gen-og should have frozen the Next-rendered image)`);
    continue;
  }
  const buf = readFileSync(file);
  const isPng = buf.length > 24 && buf[0] === 0x89 && buf.toString('ascii', 1, 4) === 'PNG';
  if (!isPng) {
    fail(`${name}: not a PNG`);
    continue;
  }
  const width = buf.readUInt32BE(16);
  const height = buf.readUInt32BE(20);
  if (width !== 1200 || height !== 630) fail(`${name}: ${width}x${height}, expected 1200x630`);
}

// --- placeholder leakage --------------------------------------------------
if (!ALLOW_PLACEHOLDER) {
  const scanned = [
    ...walk(OUT, (p) => p.endsWith('.html')),
    ...walk(OUT, (p) => p.endsWith('.xml') || p.endsWith('.txt')),
  ];
  for (const file of scanned) {
    const hit = readFileSync(file, 'utf8').match(PLACEHOLDER_RE);
    if (hit) fail(`${file.replace(OUT, 'out')}: placeholder origin leaked (${hit[0]})`);
  }
} else {
  console.warn(`check-out: placeholder mode; canonical and sitemap use ${SITE_URL}`);
}

if (failures.length) {
  console.error(`check-out failed (${failures.length}):`);
  for (const message of failures.slice(0, 40)) console.error(`  - ${message}`);
  process.exit(1);
}

const now = new Date();
const measuredAt = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
const evidence = resolve('../plans/证据/ui-v1/seo-artifacts.json');
mkdirSync(dirname(evidence), { recursive: true });
writeFileSync(
  evidence,
  `${JSON.stringify(
    {
      measuredAt,
      siteUrl: SITE_URL,
      placeholder: ALLOW_PLACEHOLDER,
      indexable,
      noindex: ['/styleguide/', '/en/styleguide/'],
      notFound: ['out/404.html', 'out/404/index.html', 'out/_not-found/index.html'],
      ogImages: ['/og/zh.png', '/og/en.png'],
      assertions: [
        'canonical equals the sitemap URL',
        '3 hreflang alternates per page, zh-CN and en differ',
        'og:url/og:image/og:title/og:description present',
        'twitter:card is summary_large_image',
        'JSON-LD parses and carries @type/@graph',
        'robots.txt declares the sitemap and disallows /styleguide/',
        'no /:3000 placeholder origin outside placeholder builds',
      ],
    },
    null,
    2
  )}\n`
);

console.log(`check-out OK: ${SITE_URL}, 12 indexable pages, 2 noindex pages, 404 noindex, OG 1200x630`);