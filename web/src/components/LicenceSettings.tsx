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
import { Button, Callout, Skeleton, Tag } from "./app/basics";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

function Card({
  title,
  text,
  icon,
  actions,
  children,
}: {
  title: string;
  text?: ReactNode;
  icon: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
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
    <a key="a" href={text.version_url} target="_blank" rel="noreferrer">
      {t.agreementName}
    </a>
  ) : (
    <span key="a">{t.agreementName}</span>
  );

  return (
    <>
      <Card
        title={t.aboutTitle}
        text={t.aboutText(link)}
        icon={<ScrollText />}
        actions={
          me.role === "super_admin" && (
            <Button asChild size="sm">
              <Link to="/licence" viewTransition>
                {t.acceptAgain}
              </Link>
            </Button>
          )
        }
      >
        <div className="card-body stack">
          {!status ? (
            <Skeleton height={96} />
          ) : (
            <>
              {status.update_needed && (
                <Callout tone="warning">{t.updateNeeded(status.required_version, status.bundled_version)}</Callout>
              )}
              {status.pending && <Callout tone="warning">{t.pending}</Callout>}
              <dl className="dl">
                <dt>{t.bundled}</dt>
                <dd>{text ? t.version(text.version, text.effective) : status.bundled_version}</dd>
                <dt>{t.required}</dt>
                <dd>{status.required_version}</dd>
                <dt>{t.accepted}</dt>
                <dd>
                  {status.accepted ? (
                    <Tag tone="positive">{status.accepted.version}</Tag>
                  ) : (
                    <Tag tone="caution">{t.notAccepted}</Tag>
                  )}
                </dd>
                {status.accepted && (
                  <>
                    <dt>{t.acceptedBy}</dt>
                    <dd>
                      <bdi>{who(status.accepted)}</bdi>
                    </dd>
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
          <Table className="licence-table">
            <TableHeader>
              <TableRow>
                <TableHead>{t.columns.when}</TableHead>
                <TableHead>{t.columns.version}</TableHead>
                <TableHead>{t.columns.who}</TableHead>
                <TableHead>{t.columns.where}</TableHead>
                <TableHead>{t.columns.use}</TableHead>
                <TableHead>{t.columns.from}</TableHead>
                <TableHead>{t.columns.hub}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {status.history.map((r) => (
                <TableRow key={r.id ?? r.accepted_at}>
                  <TableCell className="small nowrap">{fullDateTime(r.accepted_at)}</TableCell>
                  <TableCell className="small tabular">{r.version}</TableCell>
                  <TableCell className="small">
                    <bdi>{r.surface === "installer" ? `${r.user}@${r.host ?? ""}` : who(r)}</bdi>
                  </TableCell>
                  <TableCell className="small">{t.where[r.surface] ?? r.surface}</TableCell>
                  <TableCell className="small">{r.surface === "installer" ? "—" : use(r)}</TableCell>
                  <TableCell className="small mono" dir="ltr" title={r.user_agent ?? r.via ?? ""}>
                    {r.ip ?? r.via ?? ""}
                  </TableCell>
                  <TableCell className="small">
                    {r.synced_at ? <Tag tone="positive">{t.sent}</Tag> : <Tag>{t.waiting}</Tag>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
      <Card title={t.fullText} icon={<FileText />}>
        <div className="card-body">{text ? <LicenceDocument text={text} /> : <Skeleton height={120} />}</div>
      </Card>
    </>
  );
}
