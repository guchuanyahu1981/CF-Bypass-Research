import { DICT, PAGES, SITE, type Locale } from '@/content/site';
import { SITE_URL } from '@/lib/metadata';

const LOCAL_PREVIEW = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/i.test(SITE_URL);

export default function SiteFooter({ locale }: { locale: Locale }) {
  const d = DICT[locale];
  const year = new Date().getUTCFullYear();
  const updated = SITE.updatedAt;

  return (
    <footer className="footer">
      <div className="container">
        <div className="footer__grid">
          <div>
            <h2>{SITE.name}</h2>
            <p>{d.home.lede}</p>
          </div>
          <div>
            <h2>{locale === 'zh' ? '站内' : 'Site'}</h2>
            <p>
              <a href={PAGES.docs[locale]}>{d.nav.docs}</a>
              <br />
              <a href={PAGES.security[locale]}>{d.nav.security}</a>
              <br />
              <a href={PAGES.benchmarks[locale]}>{d.nav.benchmarks}</a>
            </p>
          </div>
          <div>
            <h2>{locale === 'zh' ? '仓库' : 'Repository'}</h2>
            <p>
              <a href={SITE.repo} target="_blank" rel="noopener noreferrer">
                GitHub
              </a>
              <br />
              <a href={`${SITE.repo}/blob/master/LICENSE`} target="_blank" rel="noopener noreferrer">
                {d.footer.license}
              </a>
              <br />
              <a href={`${SITE.repo}/blob/master/CHANGELOG.md`} target="_blank" rel="noopener noreferrer">
                CHANGELOG.md
              </a>
            </p>
          </div>
        </div>
        <div className="footer__legal">
          {LOCAL_PREVIEW && (
            <p className="footer__preview">
              {locale === 'zh' ? '本地预览' : 'Local preview'} · {SITE_URL} ·{' '}
              {locale === 'zh' ? '构建时未设置 NEXT_PUBLIC_SITE_URL' : 'NEXT_PUBLIC_SITE_URL was not set at build time'}
            </p>
          )}
          <p>{d.footer.disclaimer}</p>
          <p>{d.footer.trademark}</p>
          <p>
            © {year} {SITE.owner} · {d.footer.updated} {updated}
          </p>
        </div>
      </div>
    </footer>
  );
}