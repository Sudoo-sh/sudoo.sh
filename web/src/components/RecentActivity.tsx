import { CircleCheck, CircleX, History } from "lucide-react";
import { Link } from "react-router";
import { useAudit } from "@/api/queries";
import { useT } from "@/i18n";
import words from "@/i18n/expiry";
import { ago, fullDateTime } from "@/lib/format";
import { useCan } from "./Session";

/** The home page's list of the last 10 audit events (the audit log's own permission decides who sees it). */
export function RecentActivity() {
  const t = useT(words).activity;
  const can = useCan();
  const allowed = can("audit.view");
  const { data } = useAudit(allowed ? "limit=10&offset=0&sort=-ts" : "");
  if (!allowed || !data?.entries.length) return null;
  return (
    <section className="card expiry-card rise" aria-labelledby="activity-title">
      <div className="expiry-head">
        <History aria-hidden />
        <div>
          <h2 id="activity-title">{t.title}</h2>
          <p className="sub">{t.text}</p>
        </div>
        <span className="spacer" />
        <Link to="/audit" viewTransition className="small">
          {t.all}
        </Link>
      </div>
      <ul className="expiry-list">
        {data.entries.slice(0, 10).map((e, i) => (
          <li key={`${e.ts}-${i}`} className="activity-row">
            {e.ok ? (
              <CircleCheck size={15} color="var(--positive)" aria-label="" />
            ) : (
              <CircleX size={15} color="var(--negative)" aria-label={t.refused} />
            )}
            <div className="expiry-name">
              <bdi>{e.event}</bdi>
              <span className="sub truncate" dir="auto">
                {[e.user, e.instance, e.detail].filter(Boolean).join(" · ")}
              </span>
            </div>
            <span className="expiry-when" title={fullDateTime(e.ts)}>
              {ago(e.ts)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
