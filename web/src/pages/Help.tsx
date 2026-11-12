import { BookOpen } from "lucide-react";
import { useEffect, useRef } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { useDocPage, useDocs } from "@/api/queries";
import { Page } from "@/components/Shell";
import { Empty, Skeleton } from "@/components/ui/basics";

/** The documentation: the same Markdown files as in the repository's docs/, rendered by the backend. */
export function HelpPage() {
  const { pathname, hash } = useLocation();
  const navigate = useNavigate();
  const { data: contents } = useDocs();
  const path = pathname.replace(/^\/help\/?/, "") || contents?.index || "";
  const { data: page, isPending, error } = useDocPage(path);
  const article = useRef<HTMLElement>(null);

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
        <nav className="help-nav" aria-label="Documentation">
          {contents?.sections.map((section) => (
            <div key={section.title} style={{ display: "contents" }}>
              <div className="group">{section.title}</div>
              {section.pages.map((p) => (
                <Link key={p.path} to={`/help/${p.path}`} viewTransition className={p.path === path ? "active" : ""}>
                  {p.title}
                </Link>
              ))}
            </div>
          ))}
        </nav>
        {error ? (
          <div className="card"><Empty icon={<BookOpen />} title="This page does not exist">Choose another page on the left.</Empty></div>
        ) : isPending || !page ? (
          <div className="stack"><Skeleton height={40} width="50%" /><Skeleton height={400} /></div>
        ) : (
          <article ref={article} className="prose" key={page.path} dangerouslySetInnerHTML={{ __html: page.html }} />
        )}
        <nav className="help-toc" aria-label="On this page">
          {page && page.headings.length > 0 && <div className="group">On this page</div>}
          {page?.headings.map((h) => (
            <a key={h.id} href={`#${h.id}`} className={h.level === 3 ? "h3" : ""}>{h.text}</a>
          ))}
        </nav>
      </div>
    </Page>
  );
}
