// Settings, About: the Sudoo.sh Licence Agreement. The version this release bundles, the required version (a
// setting, passed in as `children`), who accepted it and when, the full text, and every acceptance (the installer's
// too), with whether the hub has it.

import { FileText, History, ScrollText } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { useLicence, useLicenceText } from "@/api/queries";
import type { LicenceRecord } from "@/api/types";
import { useT } from "@/i18n";
import words from "@/i18n/licence";
import { fullDateTime } from "@/lib/format";
import { LicenceDocument } from "./LicenceAcceptance";
import { useMe } from "./Session";
import { Callout, Skeleton, Tag } from "./ui/basics";

function Card({ title, text, icon, actions, children }: { title: string; text?: ReactNode; icon: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <div className="card-head">
        {icon}
        <div className="spacer">
          <h2>{title}</h2>
          {text && <div className="sub">{text}</div>}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function LicenceSettings({ children }: { children: ReactNode }) {
  const me = useMe();
  const { data: status } = useLicence();
  const { data: text } = useLicenceText();
  const t = useT(words);
  const who = (r: LicenceRecord) => r.user_name || r.user;
  const use = (r: LicenceRecord) => (r.use ? t.uses[r.use].title + (r.on_behalf ? ` (${t.onBehalf})` : "") : "—");
  const link = text ? (
    <a key="a" href={text.version_url} target="_blank" rel="noreferrer">{t.agreementName}</a>
  ) : (
    <span key="a">{t.agreementName}</span>
  );

  return (
    <>
      <Card title={t.aboutTitle} text={t.aboutText(link)} icon={<ScrollText />}
        actions={me.role === "super_admin" && <Link className="btn sm" to="/licence" viewTransition>{t.acceptAgain}</Link>}>
        <div className="card-body stack">
          {!status ? (
            <Skeleton height={96} />
          ) : (
            <>
              {status.update_needed && <Callout tone="warning">{t.updateNeeded(status.required_version, status.bundled_version)}</Callout>}
              {status.pending && <Callout tone="warning">{t.pending}</Callout>}
              <dl className="dl">
                <dt>{t.bundled}</dt>
                <dd>{text ? t.version(text.version, text.effective) : status.bundled_version}</dd>
                <dt>{t.required}</dt>
                <dd>{status.required_version}</dd>
                <dt>{t.accepted}</dt>
                <dd>{status.accepted ? <Tag tone="positive">{status.accepted.version}</Tag> : <Tag tone="caution">{t.notAccepted}</Tag>}</dd>
                {status.accepted && (
                  <>
                    <dt>{t.acceptedBy}</dt>
                    <dd><bdi>{who(status.accepted)}</bdi></dd>
                    <dt>{t.acceptedAt}</dt>
                    <dd>{fullDateTime(status.accepted.accepted_at)}</dd>
                    <dt>{t.declaredUse}</dt>
                    <dd>{use(status.accepted)}</dd>
                  </>
                )}
              </dl>
            </>
          )}
        </div>
        {children}
      </Card>
      {status?.history && status.history.length > 0 && (
        <Card title={t.history} text={t.historyText} icon={<History />}>
          <div className="table-wrap">
            <table className="table licence-table">
              <thead>
                <tr>
                  <th>{t.columns.when}</th>
                  <th>{t.columns.version}</th>
                  <th>{t.columns.who}</th>
                  <th>{t.columns.where}</th>
                  <th>{t.columns.use}</th>
                  <th>{t.columns.from}</th>
                  <th>{t.columns.hub}</th>
                </tr>
              </thead>
              <tbody>
                {status.history.map((r) => (
                  <tr key={r.id ?? r.accepted_at}>
                    <td className="small nowrap">{fullDateTime(r.accepted_at)}</td>
                    <td className="small tabular">{r.version}</td>
                    <td className="small"><bdi>{r.surface === "installer" ? `${r.user}@${r.host ?? ""}` : who(r)}</bdi></td>
                    <td className="small">{t.where[r.surface] ?? r.surface}</td>
                    <td className="small">{r.surface === "installer" ? "—" : use(r)}</td>
                    <td className="small mono" dir="ltr" title={r.user_agent ?? r.via ?? ""}>{r.ip ?? r.via ?? ""}</td>
                    <td className="small">{r.synced_at ? <Tag tone="positive">{t.sent}</Tag> : <Tag>{t.waiting}</Tag>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      <Card title={t.fullText} icon={<FileText />}>
        <div className="card-body">{text ? <LicenceDocument text={text} /> : <Skeleton height={120} />}</div>
      </Card>
    </>
  );
}
