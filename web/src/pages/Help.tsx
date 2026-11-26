import { BookOpen } from "lucide-react";
import { useEffect, useRef } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useDocPage, useDocs } from "@/api/queries";
import { Page, useMe } from "@/components/Shell";
import { Empty, Skeleton } from "@/components/app/basics";
import { useT } from "@/i18n";
import words from "@/i18n/operations";
import tourWords from "@/i18n/tour";
import { TOUR_RESTART_EVENT } from "@/lib/tour";

/** The documentation: the same Markdown files as in the repository's docs/, rendered by the backend. */
export function HelpPage() {
  const { pathname, hash } = useLocation();
  const navigate = useNavigate();
  const { data: contents } = useDocs();
  const path = pathname.replace(/^\/help\/?/, "") || contents?.index || "";
  const { data: page, isPending, error } = useDocPage(path);
  const article = useRef<HTMLElement>(null);
  const t = useT(words).help;
  const tour = useT(tourWords);
  const tours = useMe().tours;

  // links inside the page stay in the app; anchors scroll
  useEffect(() => {
    const el = article.current;
    if (!el) return;
    const onClick = (event: MouseEvent) => {
      const link = (event.target as HTMLElement).closest("a");
      const href = link?.getAttribute("href");
      if (!link || !href || event.metaKey || event.ctrlKey) return;
      if (href.startsWith("/help/")) {
        event.preventDefault();
        navigate(href, { viewTransition: true });
      }
    };
    el.addEventListener("click", onClick);
    return () => el.removeEventListener("click", onClick);
  }, [navigate, page]);
  useEffect(() => {
    if (hash) document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView();
    else window.scrollTo(0, 0);
  }, [page, hash]);

  return (
    <Page wide>
      <div className="help">
        <nav className="help-nav" aria-label={t.contents}>
          {contents?.sections.map((section) => (
            <div key={section.title} style={{ display: "contents" }} lang="en" dir="ltr">
              <div className="group">{section.title}</div>
              {section.pages.map((p) => (
                <Link key={p.path} to={`/help/${p.path}`} viewTransition className={p.path === path ? "active" : ""}>
                  {p.title}
                </Link>
              ))}
            </div>
          ))}
          {tours.enabled ? (
            <button
              type="button"
              className="group"
              title={tour.replayHint}
              style={{
                background: "none",
                border: 0,
                textAlign: "start",
                cursor: "pointer",
                textDecoration: "underline",
              }}
              onClick={() => window.dispatchEvent(new Event(TOUR_RESTART_EVENT))}
            >
              {tour.replay}
            </button>
          ) : (
            <div className="group">{tour.off}</div>
          )}
        </nav>
        {error ? (
          <div className="card">
            <Empty icon={<BookOpen />} title={t.missing}>
              {t.missingText}
            </Empty>
          </div>
        ) : isPending || !page ? (
          <div className="stack">
            <Skeleton height={40} width="50%" />
            <Skeleton height={400} />
          </div>
        ) : (
          <article
            ref={article}
            className="prose"
            key={page.path}
            lang="en"
            dir="ltr"
            dangerouslySetInnerHTML={{ __html: page.html }}
          />
        )}
        <nav className="help-toc" aria-label={t.onThisPage}>
          {page && page.headings.length > 0 && <div className="group">{t.onThisPage}</div>}
          {page?.headings.map((h) => (
            <a key={h.id} href={`#${h.id}`} className={h.level === 3 ? "h3" : ""} lang="en" dir="auto">
              {h.text}
            </a>
          ))}
        </nav>
      </div>
    </Page>
  );
}
