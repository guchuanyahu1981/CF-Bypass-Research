import { test, expect } from '@playwright/test';

declare global {
  interface Window {
    __cls: number;
    __CF_SCENE_METRICS?: boolean;
    __sceneFrames?: number;
  }
}

const ROUTES = [
  '/',
  '/how-it-works/',
  '/benchmarks/',
  '/docs/',
  '/security/',
  '/changelog/',
  '/en/',
  '/en/how-it-works/',
  '/en/benchmarks/',
  '/en/docs/',
  '/en/security/',
  '/en/changelog/',
];

test.describe('runtime', () => {
  for (const route of ROUTES) {
    test(`${route} loads without runtime errors`, async ({ page }) => {
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
      page.on('console', (message) => {
        if (message.type() === 'error') errors.push(`console: ${message.text()}`);
      });
      await page.goto(route, { waitUntil: 'networkidle' });
      expect(errors).toEqual([]);
      await expect(page.locator('h1')).toHaveCount(1);
      await expect(page.locator('link[rel="canonical"]')).toHaveCount(1);
    });
  }
});

test('keyboard reaches controls with a visible focus ring', async ({ page }) => {
  await page.goto('/');
  const visited = new Set<string>();
  for (let index = 0; index < 30; index += 1) {
    await page.keyboard.press('Tab');
    const focused = await page.evaluate(() => {
      const element = document.activeElement as HTMLElement | null;
      if (!element || element === document.body) return null;
      const style = getComputedStyle(element);
      return {
        tag: element.tagName,
        label: (element.getAttribute('aria-label') || element.textContent || '').trim().slice(0, 40),
        outlineStyle: style.outlineStyle,
        outlineWidth: style.outlineWidth,
        boxShadow: style.boxShadow,
      };
    });
    if (!focused) continue;
    visited.add(`${focused.tag}:${focused.label}`);
    const hasRing =
      (focused.outlineStyle !== 'none' && focused.outlineWidth !== '0px') || focused.boxShadow !== 'none';
    expect(hasRing, `no visible focus ring on ${focused.tag} "${focused.label}"`).toBe(true);
  }
  expect(visited.size).toBeGreaterThan(3);
});

test.describe('reduced motion', () => {
  test.use({ reducedMotion: 'reduce' });

  test('no canvas and no meaningful transitions', async ({ page }) => {
    await page.goto('/', { waitUntil: 'networkidle' });
    await expect(page.locator('canvas')).toHaveCount(0);
    const slow = await page.evaluate(() => {
      return [...document.querySelectorAll<HTMLElement>('body *')]
        .map((element) => parseFloat(getComputedStyle(element).transitionDuration) || 0)
        .filter((seconds) => seconds > 0.001);
    });
    expect(slow).toEqual([]);
  });
});

test('scene controls select a node and update the text panel', async ({ page }) => {
  await page.goto('/', { waitUntil: 'networkidle' });

  const wall = page.locator('[data-variant="wall"]');
  const wallButtons = wall.locator('.scene__controls button');
  await expect(wallButtons).toHaveCount(3);
  const panel = wall.locator('.scene__panel');
  const before = await panel.innerText();
  await wallButtons.nth(1).click();
  await expect(wallButtons.nth(1)).toHaveAttribute('aria-pressed', 'true');
  await expect(panel).not.toHaveText(before);
  await wallButtons.nth(0).focus();
  await expect(wallButtons.nth(0)).toBeFocused();

  const pipeline = page.locator('[data-variant="pipeline"]');
  await expect(pipeline.locator('.scene__controls button')).toHaveCount(5);

  // Labels carry the measured value, not just the node name.
  await expect(wall.locator('.scene__label').first()).toContainText(/\d/);
});

test('scene falls back to static but keeps its controls', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/', { waitUntil: 'networkidle' });
  await expect(page.locator('canvas')).toHaveCount(0);
  await expect(page.locator('[data-variant="wall"] .scene__controls button')).toHaveCount(3);
  await expect(page.locator('[data-variant="wall"]')).toHaveAttribute('data-scene', 'static');
});

test('3D scene is idle while the pointer is still', async ({ page }) => {
  await page.goto('/', { waitUntil: 'networkidle' });
  const tier = await page.locator('[data-variant="wall"]').getAttribute('data-scene');
  test.skip(tier !== 'full', `scene tier is ${tier} in this environment`);

  await page.evaluate(() => {
    window.__CF_SCENE_METRICS = true;
    window.__sceneFrames = 0;
  });
  await page.waitForTimeout(1500); // let the pointer-driven camera settle
  const start = await page.evaluate(() => window.__sceneFrames ?? 0);
  await page.waitForTimeout(1000);
  const end = await page.evaluate(() => window.__sceneFrames ?? 0);
  expect(end - start).toBeLessThanOrEqual(1);
});

test('layout shift stays below 0.05', async ({ page }) => {
  await page.goto('/', { waitUntil: 'networkidle' });
  await page.evaluate(() => {
    window.__cls = 0;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as Array<PerformanceEntry & { hadRecentInput: boolean; value: number }>) {
        if (!entry.hadRecentInput) window.__cls += entry.value;
      }
    }).observe({ type: 'layout-shift', buffered: true });
  });
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(1500);
  expect(await page.evaluate(() => window.__cls)).toBeLessThan(0.05);
});