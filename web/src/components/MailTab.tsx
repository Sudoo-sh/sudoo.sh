// An instance's Mail tab (staging and development): the e-mails Odoo sent, caught by the mail catcher. A list with
// search on one side and the e-mail on the other (one after the other on a phone): its HTML in a sandboxed frame
// served with its own strict Content-Security-Policy, its text, its headers and attachments; administrators delete
// and release (send a copy to an address).

import { ArrowLeft, Download, Inbox, Mail, Paperclip, RefreshCw, Search, Send, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, errorText } from "@/api/client";
import { keys, useCall, useCaughtMail, useCaughtMailDetail } from "@/api/queries";
import type { CaughtMail, CaughtMailDetail, Instance } from "@/api/types";
import { useCan } from "@/components/Shell";
import { Button, Callout, Empty, Field, Segmented, Skeleton } from "@/components/ui/basics";
import { Modal, useToast } from "@/components/ui/overlay";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/mail";
import { ago, bytes, fullDateTime } from "@/lib/format";

const PAGE = 50;
const DEMO = import.meta.env.MODE === "demo";

const base = (name: string, id?: string) => `/api/v1/instances/${encodeURIComponent(name)}/mail${id ? `/${id}` : ""}`;

export function MailTab({ instance }: { instance: Instance }) {
  const name = instance.name;
  const can = useCan();
  const manage = can("caught_mail.manage");
  const t = useT(words);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [selected, setSelected] = useState<string | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);
  const { data, isPending, refetch, isFetching } = useCaughtMail(name, search, limit);
  useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [query]);
  const items = data?.items ?? [];
  const meta = data?.meta;
  // the selected e-mail was deleted (here, by someone else, or by the retention): back to the list
  useEffect(() => {
    if (selected && data && !items.some((m) => m.id === selected) && !search) setSelected(null);
  }, [data, items, selected, search]);

  return (
    <div className="caught-mail-tab stack">
      {meta && !meta.catcher.running && <Callout tone="warning">{meta.catcher.exists ? t.catcherStopped : t.catcherMissing}</Callout>}
      <div className="card">
        <div className="card-head">
          <h2>{t.title}</h2>
          <span className="sub">{t.sub}</span>
          <span className="spacer" />
          <Button size="sm" variant="ghost" icon={<RefreshCw />} loading={isFetching && !isPending} aria-label={t.refresh} title={t.refresh} onClick={() => refetch()} />
          {manage && (meta?.total ?? 0) > 0 && !search && (
            <Button size="sm" icon={<Trash2 />} onClick={() => setConfirmAll(true)}>
              {t.deleteAll}
            </Button>
          )}
        </div>
        <div className="card-body caught-mail-toolbar">
          <label className="input-group caught-mail-search">
            <Search aria-hidden="true" />
            <input className="input" type="search" value={query} placeholder={t.search} aria-label={t.searchLabel} onChange={(e) => setQuery(e.target.value)} />
          </label>
          {meta && <span className="xsmall faint">{t.usage(meta.usage.messages, bytes(meta.usage.bytes), meta.limits.retention_days)}</span>}
        </div>
        {isPending ? (
          <div className="card-body stack-sm"><Skeleton height={48} /><Skeleton height={48} /><Skeleton height={48} /></div>
        ) : items.length === 0 ? (
          <Empty icon={<Inbox />} title={search ? t.noMatch : t.emptyTitle}>{search ? null : t.empty}</Empty>
        ) : (
          <div className="caught-mail-split" data-open={selected ? "" : undefined}>
            <div className="caught-mail-list-wrap">
              <ul className="caught-mail-list" aria-label={t.title}>
                {items.map((mail) => (
                  <MailRow key={mail.id} mail={mail} active={mail.id === selected} onOpen={() => setSelected(mail.id)} />
                ))}
              </ul>
              {meta && meta.total > items.length && (
                <div className="card-body"><Button size="sm" block onClick={() => setLimit(limit + PAGE)}>{t.more}</Button></div>
              )}
            </div>
            <div className="caught-mail-view">
              {selected ? (
                <Viewer name={name} id={selected} manage={manage} release={!!meta?.release} onBack={() => setSelected(null)} />
              ) : (
                <div className="caught-mail-pick"><Mail aria-hidden="true" /><span className="muted small">{t.pick}</span></div>
              )}
            </div>
          </div>
        )}
      </div>
      {confirmAll && meta && <DeleteAll name={name} count={meta.total} onClose={() => setConfirmAll(false)} onDone={() => setSelected(null)} />}
    </div>
  );
}

function MailRow({ mail, active, onOpen }: { mail: CaughtMail; active: boolean; onOpen: () => void }) {
  const t = useT(words);
  const recipients = mail.to.length ? mail.to : mail.rcpt_to;
  return (
    <li>
      <button type="button" className={`caught-mail-row ${active ? "active" : ""}`} aria-current={active || undefined} onClick={onOpen}>
        <span className="caught-mail-row-top">
          <bdi className="caught-mail-from truncate">{mail.from || mail.mail_from}</bdi>
          <span className="xsmall faint" title={fullDateTime(mail.received_at)}>{ago(mail.received_at)}</span>
        </span>
        <span className="caught-mail-subject truncate">
          {mail.attachments > 0 && <Paperclip aria-hidden="true" />}
          <bdi>{mail.subject || t.noSubject}</bdi>
        </span>
        <span className="xsmall muted truncate" dir="auto">{t.to}: {recipients.join(", ")}</span>
      </button>
    </li>
  );
}

type View = "html" | "text" | "headers";

function Viewer({ name, id, manage, release, onBack }: { name: string; id: string; manage: boolean; release: boolean; onBack: () => void }) {
  const { data: mail } = useCaughtMailDetail(name, id);
  const t = useT(words);
  const [view, setView] = useState<View>("html");
  const [dialog, setDialog] = useState<null | "delete" | "release">(null);
  useEffect(() => setView("html"), [id]);
  // on a phone the e-mail replaces the list: start reading at its top
  useEffect(() => {
    if (matchMedia("(max-width: 860px)").matches) document.querySelector(".caught-mail-view")?.scrollIntoView({ block: "start" });
  }, [id]);
  if (!mail) return <div className="card-body stack-sm"><Skeleton height={28} width="70%" /><Skeleton height={240} /></div>;
  const shown: View = view === "html" && !mail.has_html ? "text" : view;
  return (
    <article className="caught-mail-detail" aria-labelledby="caught-mail-subject">
      <div className="caught-mail-detail-head">
        <Button className="caught-mail-back" size="sm" variant="ghost" icon={<ArrowLeft className="flip-rtl" />} onClick={onBack}>{t.back}</Button>
        <h3 id="caught-mail-subject"><bdi>{mail.subject || t.noSubject}</bdi></h3>
        <dl className="caught-mail-meta">
          <div><dt>{t.from}</dt><dd><bdi>{mail.from || mail.mail_from}</bdi></dd></div>
          {mail.to.length > 0 && <div><dt>{t.to}</dt><dd dir="ltr">{mail.to.join(", ")}</dd></div>}
          {mail.cc.length > 0 && <div><dt>{t.cc}</dt><dd dir="ltr">{mail.cc.join(", ")}</dd></div>}
          <div><dt>{t.envelope}</dt><dd dir="ltr">{mail.rcpt_to.join(", ")}</dd></div>
          <div><dt>{t.received}</dt><dd>{fullDateTime(mail.received_at)}</dd></div>
          <div><dt>{t.size}</dt><dd>{bytes(mail.size)}</dd></div>
        </dl>
        <div className="caught-mail-actions">
          <a className="btn sm" href={`${base(name, id)}/raw`} download><Download aria-hidden="true" /> {t.download}</a>
          {manage && release && <Button size="sm" icon={<Send className="flip-rtl" />} onClick={() => setDialog("release")}>{t.release}</Button>}
          {manage && <Button size="sm" icon={<Trash2 />} onClick={() => setDialog("delete")}>{t.delete}</Button>}
        </div>
      </div>
      {mail.attachment_list.length > 0 && (
        <div className="caught-mail-attachments" aria-label={t.attachments}>
          {mail.attachment_list.map((a) => (
            <a key={a.index} className="caught-mail-attachment" href={`${base(name, id)}/attachments/${a.index}`} download={a.filename}>
              <Paperclip aria-hidden="true" />
              <bdi className="truncate">{a.filename}</bdi>
              <span className="xsmall faint">{bytes(a.size)}</span>
            </a>
          ))}
        </div>
      )}
      <Segmented<View>
        value={shown}
        onChange={setView}
        options={[
          { value: "html", label: t.html, disabled: !mail.has_html },
          { value: "text", label: t.text },
          { value: "headers", label: t.headers },
        ]}
      />
      <div className="caught-mail-body">
        {shown === "html" && <HtmlBody name={name} mail={mail} />}
        {shown === "text" && (mail.text ? <pre className="caught-mail-text" dir="auto">{mail.text}</pre> : <p className="muted small">{t.noText}</p>)}
        {shown === "headers" && (
          <div className="table-wrap">
            <table className="table compact caught-mail-headers" dir="ltr">
              <tbody>
                {mail.headers.map(([key, value], i) => (
                  <tr key={`${key}${i}`}><th className="mono">{key}</th><td className="mono">{value}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {dialog === "delete" && <DeleteOne name={name} mail={mail} onClose={() => setDialog(null)} onDone={onBack} />}
      {dialog === "release" && <Release name={name} mail={mail} onClose={() => setDialog(null)} />}
    </article>
  );
}

/** The e-mail's HTML: never in the panel's own page, but in a frame with the sandbox attribute (no scripts, no
 *  forms, no same-origin access), loaded from the backend, which serves it with its own strict policy. The demo has
 *  no backend to load it from, so it writes the HTML into the frame (the sandbox still applies). */
function HtmlBody({ name, mail }: { name: string; mail: CaughtMailDetail }) {
  const t = useT(words);
  const title = t.frameTitle(mail.subject || t.noSubject);
  return (
    <div className="stack-sm">
      {DEMO ? (
        <iframe className="caught-mail-frame" title={title} sandbox="" referrerPolicy="no-referrer" srcDoc={mail.html ?? ""} />
      ) : (
        <iframe className="caught-mail-frame" title={title} sandbox="" referrerPolicy="no-referrer" src={`${base(name, mail.id)}/html`} />
      )}
      <span className="xsmall faint">{t.blocked}</span>
    </div>
  );
}

function DeleteOne({ name, mail, onClose, onDone }: { name: string; mail: CaughtMail; onClose: () => void; onDone: () => void }) {
  const t = useT(words);
  const c = useT(common).common;
  const toast = useToast();
  const remove = useCall(() => api.del(`/instances/${name}/mail/${mail.id}`), [keys.mail(name)]);
  return (
    <Modal title={t.deleteTitle} description={t.deleteText} onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{c.cancel}</Button>
          <Button variant="danger" loading={remove.isPending}
            onClick={() => remove.mutate(undefined, {
              onSuccess: () => { toast("success", t.deleted); onClose(); onDone(); },
              onError: (e) => toast("error", t.notDeleted, errorText(e)),
            })}>
            {t.delete}
          </Button>
        </>
      } />
  );
}

function DeleteAll({ name, count, onClose, onDone }: { name: string; count: number; onClose: () => void; onDone: () => void }) {
  const t = useT(words);
  const c = useT(common).common;
  const toast = useToast();
  const remove = useCall(() => api.del(`/instances/${name}/mail`), [keys.mail(name)]);
  return (
    <Modal title={t.deleteAllTitle} description={t.deleteAllText(count)} onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{c.cancel}</Button>
          <Button variant="danger" loading={remove.isPending}
            onClick={() => remove.mutate(undefined, {
              onSuccess: () => { toast("success", t.allDeleted); onClose(); onDone(); },
              onError: (e) => toast("error", t.notDeleted, errorText(e)),
            })}>
            {t.deleteAll}
          </Button>
        </>
      } />
  );
}

function Release({ name, mail, onClose }: { name: string; mail: CaughtMail; onClose: () => void }) {
  const t = useT(words);
  const c = useT(common).common;
  const toast = useToast();
  const [to, setTo] = useState("");
  const [error, setError] = useState("");
  const send = useCall(() => api.post(`/instances/${name}/mail/${mail.id}/release`, { to: to.trim() }));
  const submit = () => {
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(to.trim())) return setError(t.badAddress);
    send.mutate(undefined, {
      onSuccess: () => { toast("success", t.released(to.trim())); onClose(); },
      onError: (e) => setError(errorText(e)),
    });
  };
  return (
    <Modal title={t.releaseTitle} description={t.releaseText} onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{c.cancel}</Button>
          <Button variant="primary" icon={<Send className="flip-rtl" />} loading={send.isPending} onClick={submit}>{t.releaseSend}</Button>
        </>
      }>
      <form onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <Field label={t.releaseTo} htmlFor="release-to" error={error}>
          <input id="release-to" className="input" type="email" dir="ltr" autoComplete="email" value={to}
            onChange={(e) => { setTo(e.target.value); setError(""); }} autoFocus />
        </Field>
      </form>
    </Modal>
  );
}
