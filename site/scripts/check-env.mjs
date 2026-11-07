// Fails a production build when the public origin is missing or is a placeholder.
// Local previews opt in explicitly: SITE_ALLOW_PLACEHOLDER=1.
import { pathToFileURL } from 'node:url';

const PLACEHOLDER_HOST = /^(localhost\.?|127\.\d+\.\d+\.\d+|\[::1\]|0\.0\.0\.0)$/i;
const RESERVED_HOST = /(^|\.)(example\.(com|org|net)|example|invalid|test)$/i;

export function resolveSiteUrl(env = process.env) {
  const raw = (env.NEXT_PUBLIC_SITE_URL || '').trim();
  const allowPlaceholder = env.SITE_ALLOW_PLACEHOLDER === '1';

  if (!raw) {
    if (allowPlaceholder) {
      return { url: 'http://localhost:3000', placeholder: true, reason: 'NEXT_PUBLIC_SITE_URL is unset' };
    }
    throw new Error(
      [
        'NEXT_PUBLIC_SITE_URL is required for a production build.',
        'Set it to the public origin, for example:',
        '  NEXT_PUBLIC_SITE_URL=https://your-domain npm run build',
        'For a local-only preview build, opt in explicitly:',
        '  SITE_ALLOW_PLACEHOLDER=1 npm run build',
      ].join('\n')
    );
  }

  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error(`NEXT_PUBLIC_SITE_URL is not a valid absolute URL: ${raw}`);
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error(`NEXT_PUBLIC_SITE_URL must use http or https: ${raw}`);
  }
  if (parsed.pathname !== '/' || parsed.search || parsed.hash || parsed.username || parsed.password) {
    throw new Error(`NEXT_PUBLIC_SITE_URL must be an origin without a path, query or credentials: ${raw}`);
  }

  const host = parsed.hostname;
  const isPlaceholder = PLACEHOLDER_HOST.test(host) || RESERVED_HOST.test(host);
  if (isPlaceholder && !allowPlaceholder) {
    throw new Error(
      [
        `NEXT_PUBLIC_SITE_URL points at a placeholder host: ${host}`,
        'Canonical, hreflang, sitemap, robots and Open Graph would all be wrong.',
        'Set the real public origin, or opt into a local preview build with SITE_ALLOW_PLACEHOLDER=1.',
      ].join('\n')
    );
  }

  return { url: parsed.origin, placeholder: isPlaceholder, reason: isPlaceholder ? host : '' };
}

function main() {
  try {
    const { url, placeholder, reason } = resolveSiteUrl();
    if (placeholder) {
      console.warn(`check-env: placeholder build, ${reason}; canonical and sitemap will point at ${url}`);
    } else {
      console.log(`check-env: site url ${url}`);
    }
  } catch (error) {
    console.error(`check-env: ${error.message}`);
    process.exit(1);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();