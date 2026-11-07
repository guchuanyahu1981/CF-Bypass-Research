import type { Metadata } from 'next';
import { DICT, PAGES, SITE, type Locale, type NavKey } from '@/content/site';

export const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'http://localhost:3000';
export const metadataBase = new URL(SITE_URL);

export function ogImageFor(locale: Locale): string {
  return locale === 'zh' ? '/og/zh.png' : '/og/en.png';
}

export function buildMetadata(locale: Locale, key: NavKey): Metadata {
  const d = DICT[locale];
  const meta = d.meta[key];
  const path = PAGES[key][locale];
  const image = { url: ogImageFor(locale), width: 1200, height: 630, alt: meta.title };
  return {
    metadataBase,
    title: meta.title,
    description: meta.description,
    alternates: {
      canonical: path,
      languages: {
        'zh-CN': PAGES[key].zh,
        en: PAGES[key].en,
        'x-default': PAGES[key].zh,
      },
    },
    openGraph: {
      title: meta.title,
      description: meta.description,
      url: path,
      siteName: SITE.name,
      locale: locale === 'zh' ? 'zh_CN' : 'en_US',
      type: 'website',
      images: [image],
    },
    twitter: {
      card: 'summary_large_image',
      title: meta.title,
      description: meta.description,
      images: [image],
    },
  };
}

export function styleguideMetadata(locale: Locale): Metadata {
  const d = DICT[locale];
  return {
    metadataBase,
    title: `${d.styleguide.title} — ${SITE.name}`,
    description: d.styleguide.lede,
    robots: { index: false, follow: false },
  };
}