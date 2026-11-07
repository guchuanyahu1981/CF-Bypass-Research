import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { DICT, NAV_KEYS, PAGES, SITE } from '../../content/site.ts';
import { BENCHMARKS } from '../../data/benchmarks.ts';

const LOCALES = ['zh', 'en'];

function sameShape(a, b, at = '') {
  assert.equal(typeof a, typeof b, `${at}: type`);
  if (Array.isArray(a)) {
    assert.equal(a.length, b.length, `${at}: array length`);
    a.forEach((item, index) => sameShape(item, b[index], `${at}[${index}]`));
    return;
  }
  if (a && typeof a === 'object') {
    assert.deepEqual(Object.keys(a).sort(), Object.keys(b).sort(), `${at}: keys`);
    for (const key of Object.keys(a)) sameShape(a[key], b[key], `${at}.${key}`);
  }
}

test('zh/en dictionaries have identical shape', () => {
  sameShape(DICT.zh, DICT.en, 'DICT');
});

test('every nav page has metadata in both locales', () => {
  for (const locale of LOCALES) {
    for (const key of NAV_KEYS) {
      const meta = DICT[locale].meta[key];
      assert.ok(meta, `${locale}.${key}: missing meta`);
      assert.ok(meta.title.length > 0, `${locale}.${key}: empty title`);
      assert.ok(meta.description.length > 0, `${locale}.${key}: empty description`);
    }
  }
});

test('titles are unique and within length limits', () => {
  const titles = [];
  for (const locale of LOCALES) {
    const max = locale === 'zh' ? 45 : 60;
    for (const key of NAV_KEYS) {
      const title = DICT[locale].meta[key].title;
      assert.ok(title.length <= max, `${locale}.${key}: title ${title.length} > ${max}`);
      titles.push(title);
    }
  }
  assert.equal(new Set(titles).size, titles.length, 'duplicate titles');
});

test('descriptions are unique and 80–160 chars', () => {
  const descriptions = [];
  for (const locale of LOCALES) {
    for (const key of NAV_KEYS) {
      const description = DICT[locale].meta[key].description;
      assert.ok(description.length >= 80, `${locale}.${key}: description ${description.length} < 80`);
      assert.ok(description.length <= 160, `${locale}.${key}: description ${description.length} > 160`);
      descriptions.push(description);
    }
  }
  assert.equal(new Set(descriptions).size, descriptions.length, 'duplicate descriptions');
});

test('indexable surface is six pages per locale (12 total)', () => {
  const indexable = Object.values(PAGES).filter((page) => page.index);
  assert.equal(indexable.length, 6);
  assert.equal(indexable.length * 2, 12);
});

test('benchmark schema, ids, dates and source paths', () => {
  const ids = new Set();
  for (const b of BENCHMARKS) {
    assert.ok(b.id, 'missing id');
    assert.ok(!ids.has(b.id), `duplicate benchmark id ${b.id}`);
    ids.add(b.id);
    assert.ok(b.label.zh && b.label.en, `${b.id}: missing label`);
    assert.ok(b.conditions.zh && b.conditions.en, `${b.id}: missing conditions`);
    assert.ok(Number.isFinite(b.value), `${b.id}: value not finite`);
    if (b.valueMax !== undefined) assert.ok(b.valueMax >= b.value, `${b.id}: valueMax < value`);
    assert.match(b.measuredAt, /^\d{4}-\d{2}(-\d{2})?$/, `${b.id}: bad date ${b.measuredAt}`);
    assert.ok(['verified', 'partial', 'limitation'].includes(b.status), `${b.id}: bad status`);
    const source = b.source.split('#')[0];
    assert.ok(fs.existsSync(path.resolve('..', source)), `${b.id}: source missing ${source}`);
  }
});

test('SEO keyword rows point to real pages and are fresh', () => {
  const data = JSON.parse(fs.readFileSync('seo/keywords.json', 'utf8'));
  assert.ok(data.rows.length >= 10);
  const paths = new Set(
    Object.values(PAGES).flatMap((page) => [page.zh, page.en])
  );
  for (const row of data.rows) {
    assert.ok(row.zh && row.en, 'keyword row missing zh/en');
    assert.ok(paths.has(row.page), `keyword page not in routes: ${row.page}`);
  }
  const reviewed = Date.parse(data.lastReviewed);
  assert.ok(Number.isFinite(reviewed), 'invalid lastReviewed');
  const days = Math.floor((Date.now() - reviewed) / 86_400_000);
  assert.ok(days <= (data.reviewIntervalDays ?? 120), `keywords are ${days} days old`);
});

test('home metrics reference real benchmark ids, and components do not hardcode numbers', () => {
  const ids = new Set(BENCHMARKS.map((benchmark) => benchmark.id));
  for (const locale of LOCALES) {
    for (const metric of DICT[locale].home.metrics) {
      assert.ok(ids.has(metric.benchmarkId), `${locale}: unknown benchmark id ${metric.benchmarkId}`);
    }
  }

  // §3.1: every performance number reads from data/benchmarks.ts; components must not
  // carry its own copy. Match the value together with its unit so visual constants
  // such as stopOpacity="0.55" are not mistaken for data.
  const HARDCODED = [/\b18\.43\s*(s|秒)/, /\b2\.93\s*(ms|毫秒)/, /\b0\.55\s*(s|秒)/, /\b5589\b/, /\b463\s*B\b/];
  const files = [];
  const walk = (dir) => {
    for (const name of fs.readdirSync(dir)) {
      const full = path.join(dir, name);
      if (fs.statSync(full).isDirectory()) walk(full);
      else if (name.endsWith('.tsx') || name.endsWith('.ts')) files.push(full);
    }
  };
  walk('components');
  for (const file of files) {
    const text = fs.readFileSync(file, 'utf8');
    for (const pattern of HARDCODED) {
      assert.ok(!pattern.test(text), `${file}: hardcodes benchmark value ${pattern}`);
    }
  }
});

test('legal copy stays present in both locales', () => {
  for (const locale of LOCALES) {
    const { footer, security } = DICT[locale];
    assert.match(footer.disclaimer, /授权|authorized/i, `${locale}: disclaimer lost its authorization boundary`);
    assert.match(footer.trademark, /Cloudflare/, `${locale}: trademark notice missing Cloudflare`);
    assert.match(footer.trademark, /无关联|Not affiliated/i, `${locale}: trademark notice lost non-affiliation`);
    assert.ok(security.sections.length >= 4, `${locale}: security sections shrank to ${security.sections.length}`);
    const titles = security.sections.map((section) => section.title).join(' ');
    assert.match(titles, /授权|Authorization/i, `${locale}: security page lost the authorization boundary section`);
    assert.match(titles, /非关联|Non-affiliation/i, `${locale}: security page lost the non-affiliation section`);
  }
});

test('site updatedAt is a real date that covers the latest release', () => {
  assert.match(SITE.updatedAt, /^\d{4}-\d{2}-\d{2}$/, 'updatedAt is not YYYY-MM-DD');
  assert.ok(!Number.isNaN(Date.parse(SITE.updatedAt)), 'updatedAt is not a real date');
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  assert.ok(SITE.updatedAt <= today, `updatedAt ${SITE.updatedAt} is in the future (local ${today})`);
  for (const locale of LOCALES) {
    const latest = DICT[locale].changelog.items[0].date;
    assert.ok(SITE.updatedAt >= latest, `${locale}: updatedAt ${SITE.updatedAt} predates release ${latest}`);
  }
});