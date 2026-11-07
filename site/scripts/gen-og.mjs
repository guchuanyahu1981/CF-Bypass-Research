// Freezes the Next-rendered Open Graph images into stable /og/{zh,en}.png paths.
// The file convention emits hash-suffixed routes that only the segment page references;
// a fixed .png also gets the right Content-Type from any static host.
import { readFileSync, readdirSync, statSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

const OUT = resolve('out');
const OG_DIR = join(OUT, 'og');

function sourceFor(parent) {
  if (!existsSync(parent)) return null;
  for (const name of readdirSync(parent)) {
    if (!name.startsWith('opengraph-image-')) continue;
    const path = join(parent, name);
    const stat = statSync(path);
    if (stat.isFile()) return path;
    if (stat.isDirectory()) {
      for (const child of readdirSync(path)) {
        const candidate = join(path, child);
        if (statSync(candidate).isFile()) return candidate;
      }
    }
  }
  return null;
}

const sources = {
  'zh.png': sourceFor(OUT),
  'en.png': sourceFor(join(OUT, 'en')),
};

const failures = [];
for (const [name, file] of Object.entries(sources)) {
  if (!file) {
    failures.push(`no Next-rendered Open Graph image found for ${name}`);
    continue;
  }
  const buf = readFileSync(file);
  const isPng = buf.length > 24 && buf[0] === 0x89 && buf.toString('ascii', 1, 4) === 'PNG';
  if (!isPng) {
    failures.push(`${file}: not a PNG`);
    continue;
  }
  const width = buf.readUInt32BE(16);
  const height = buf.readUInt32BE(20);
  if (width !== 1200 || height !== 630) {
    failures.push(`${file}: ${width}x${height}, expected 1200x630`);
    continue;
  }
  mkdirSync(OG_DIR, { recursive: true });
  writeFileSync(join(OG_DIR, name), buf);
  console.log(`gen-og: ${name} <- ${file.replace(OUT, 'out')} (${buf.length} bytes)`);
}

if (failures.length) {
  console.error(`gen-og failed (${failures.length}):`);
  for (const message of failures) console.error(`  - ${message}`);
  process.exit(1);
}