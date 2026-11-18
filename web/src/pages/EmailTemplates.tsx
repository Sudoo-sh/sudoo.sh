// E-mail templates: every e-mail the panel sends, with its subject, title and text in a plain format (placeholders
// and a little formatting, never template code). A live preview with sample values, "Send me a test" and "Reset to
// default"; the built-in texts stay in the panel's code. Super admins only (permission mail.manage).

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Braces, Mail, RotateCcw, Send } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useMailTemplates } from "@/api/queries";
import type { MailPreview, MailTemplate } from "@/api/types";
import { Page, PageHead, useMe } from "@/components/Shell";
import { Button, Callout, Field, Segmented, Skeleton, Tag } from "@/components/ui/basics";
import { Modal, useToast } from "@/components/ui/overlay";
import { ago } from "@/lib/format";

type Text = { subject: string; title: string; body: string };
type Part = keyof Text;

const same = (a: Text, b: Text) => a.subject === b.subject && a.title === b.title && a.body === b.body;
const textOf = (t: MailTemplate): Text => ({ subject: t.subject, title: t.title, body: t.body });

/** The value after it has stopped changing for `ms`, so the preview is asked for once typing pauses. */
function useSettled<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

function Preview({ template, text }: { template: MailTemplate; text: Text }) {
  const [view, setView] = useState<"html" | "text">("html");
  const settled = useSettled(text, 350);
  const { data, isPending } = useQuery({
    queryKey: ["mail-preview", template.key, settled],
    queryFn: () => api.post<MailPreview>(`/mail-templates/${template.key}/preview`, settled),
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  });
  return (
    <section className="card mail-preview">
      <div className="card-head">
        <div className="spacer">
          <h2>Preview</h2>
          <div className="sub">With sample values</div>
        </div>
        <Segmented value={view} onChange={setView} options={[{ value: "html", label: "E-mail" }, { value: "text", label: "Text only" }]} />
      </div>
      {isPending ? (
        <div className="card-body"><Skeleton height={420} /></div>
      ) : data?.problems.length ? (
        <div className="card-body">
          <Callout tone="danger">
            <ul className="problems">{data.problems.map((p) => <li key={p}>{p}</li>)}</ul>
          </Callout>
        </div>
      ) : (
        <>
          <div className="mail-subject">
            <span className="faint">Subject</span>
            <span className="truncate">{data?.subject}</span>
          </div>
          {view === "html" ? (
            // the e-mail is drawn in an iframe that may run nothing (no scripts, no forms, no navigation)
            <iframe className="mail-frame" title={`Preview of ${template.name}`} sandbox="" srcDoc={data?.html ?? ""} />
          ) : (
            <pre className="mail-text">{data?.text}</pre>
          )}
        </>
      )}
    </section>
  );
}

function Editor({ template, draft, onChange, mailReady }: { template: MailTemplate; draft: Text; onChange: (text: Text) => void; mailReady: boolean }) {
  const toast = useToast();
  const me = useMe();
  const client = useQueryClient();
  const fields = useRef<Record<Part, HTMLInputElement | HTMLTextAreaElement | null>>({ subject: null, title: null, body: null });
  const [focus, setFocus] = useState<Part>("body");
  const [busy, setBusy] = useState<"save" | "test" | "reset" | null>(null);
  const [resetting, setResetting] = useState(false);
  const dirty = !same(draft, textOf(template));

  /** Insert a placeholder where the cursor is, in the field used last; a block goes on a line of its own. */
  const insert = (name: string, block: boolean) => {
    const part: Part = block ? "body" : focus;
    const field = fields.current[part];
    const value = draft[part];
    const start = field?.selectionStart ?? value.length;
    const end = field?.selectionEnd ?? value.length;
    let piece = `{{ ${name} }}`;
    if (block) {
      const before = value.slice(0, start);
      piece = `${before && !before.endsWith("\n") ? "\n\n" : ""}${piece}${value.slice(end).startsWith("\n") ? "" : "\n\n"}`;
    }
    onChange({ ...draft, [part]: value.slice(0, start) + piece + value.slice(end) });
    requestAnimationFrame(() => {
      field?.focus();
      field?.setSelectionRange(start + piece.length, start + piece.length);
    });
  };

  const save = async () => {
    setBusy("save");
    try {
      await api.put(`/mail-templates/${template.key}`, draft);
      await client.invalidateQueries({ queryKey: keys.mailTemplates });
      toast("success", `${template.name} saved`, "The panel sends this text from now on");
    } catch (e) {
      toast("error", "Not saved", errorText(e));
    } finally {
      setBusy(null);
    }
  };
  const test = async () => {
    setBusy("test");
    try {
      const sent = await api.post<{ sent_to: string }>(`/mail-templates/${template.key}/test`, draft);
      toast("success", "Test e-mail sent", `To ${sent.sent_to}, with sample values${dirty ? " and your unsaved text" : ""}`);
    } catch (e) {
      toast("error", "Not sent", errorText(e));
    } finally {
      setBusy(null);
    }
  };
  const reset = async () => {
    setBusy("reset");
    try {
      await api.del(`/mail-templates/${template.key}`);
      await client.invalidateQueries({ queryKey: keys.mailTemplates });
      onChange(template.default);
      setResetting(false);
      toast("success", `${template.name} is back to the built-in text`);
    } catch (e) {
      toast("error", "Not reset", errorText(e));
    } finally {
      setBusy(null);
    }
  };

  const bind = (part: Part) => ({
    ref: (el: HTMLInputElement | HTMLTextAreaElement | null) => {
      fields.current[part] = el;
    },
    value: draft[part],
    onFocus: () => setFocus(part),
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange({ ...draft, [part]: e.target.value }),
  });

  return (
    <section className="card">
      <div className="card-head">
        <Mail />
        <div className="spacer">
          <h2>{template.name}</h2>
          <div className="sub">{template.description}</div>
          <div className="sub">Sent to: {template.recipients}</div>
        </div>
        {template.customized && <Tag tone="accent">changed</Tag>}
      </div>
      <div className="card-body stack">
        <Field label="Subject" htmlFor="mail-subject"><input id="mail-subject" className="input" spellCheck {...bind("subject")} /></Field>
        <Field label="Title" htmlFor="mail-title" hint="The large line at the top of the e-mail"><input id="mail-title" className="input" spellCheck {...bind("title")} /></Field>
        <Field label="Text" htmlFor="mail-body" hint="**bold** · - list item · > highlighted note · [label](link): a button when alone on its line · blank line: new paragraph">
          <textarea id="mail-body" className="input mail-body" rows={14} spellCheck {...bind("body")} />
        </Field>
        <div className="stack-sm">
          <span className="small muted row"><Braces size={14} /> Placeholders: click one to insert it where the cursor is</span>
          <div className="placeholders">
            {template.placeholders.map((p) => (
              <button key={p.name} type="button" className="placeholder" onClick={() => insert(p.name, p.kind === "block")} title={p.description}>
                <span className="mono">{`{{ ${p.name} }}`}</span>
                <span className="d">{p.description}{p.kind === "block" ? " (on its own line)" : p.kind === "link" ? " (a link)" : ""}</span>
              </button>
            ))}
          </div>
          {template.required.length > 0 && (
            <span className="xsmall faint">Keep {template.required.map((r) => `{{ ${r} }}`).join(" or ")}: the e-mail is useless without it.</span>
          )}
        </div>
      </div>
      <div className="card-foot row-wrap">
        <span className="xsmall faint">
          {template.customized ? `Changed ${ago(template.changed_at)}${template.changed_by ? ` by ${template.changed_by}` : ""}` : "Built-in text"}
        </span>
        <span className="spacer" />
        {template.customized && <Button variant="ghost" icon={<RotateCcw />} onClick={() => setResetting(true)}>Reset to default</Button>}
        <Button icon={<Send />} loading={busy === "test"} disabled={!mailReady || !me.email} title={!me.email ? "Your account has no e-mail address" : undefined} onClick={test}>
          Send me a test
        </Button>
        {dirty && <Button variant="ghost" onClick={() => onChange(textOf(template))}>Discard</Button>}
        <Button variant="primary" loading={busy === "save"} disabled={!dirty} onClick={save}>Save</Button>
      </div>
      {resetting && (
        <Modal title={`Reset ${template.name}?`} description="The built-in subject, title and text come back. Your changes to this e-mail are lost." onClose={() => setResetting(false)}
          footer={<><Button onClick={() => setResetting(false)}>Cancel</Button><Button variant="danger" loading={busy === "reset"} onClick={reset}>Reset</Button></>} />
      )}
    </section>
  );
}

export function EmailTemplatesPage() {
  const { data, isPending } = useMailTemplates();
  const [params, setParams] = useSearchParams();
  const [drafts, setDrafts] = useState<Record<string, Text>>({});
  const templates = data?.templates ?? [];
  const chosen = templates.find((t) => t.key === params.get("e")) ?? templates[0];
  const draft = chosen ? drafts[chosen.key] ?? textOf(chosen) : null;
  const unsaved = useMemo(
    () => new Set(templates.filter((t) => drafts[t.key] && !same(drafts[t.key]!, textOf(t))).map((t) => t.key)),
    [templates, drafts],
  );
  // after a save or reset, the saved text is the draft: forget the copy
  useEffect(() => {
    setDrafts((current) => {
      const kept = Object.fromEntries(Object.entries(current).filter(([key, text]) => {
        const t = templates.find((x) => x.key === key);
        return t && !same(text, textOf(t));
      }));
      return Object.keys(kept).length === Object.keys(current).length ? current : kept;
    });
  }, [templates]);
  const select = (key: string) => setParams({ e: key }, { preventScrollReset: true, replace: true });
  return (
    <Page wide>
      <PageHead title="E-mail templates">
        Every e-mail the panel sends. Change a subject or a text here; the built-in text stays, and Reset to default brings it back.
      </PageHead>
      {isPending || !chosen || !draft ? (
        <Skeleton height={480} />
      ) : (
        <>
          {!data?.mailReady && (
            <Callout tone="warning">
              E-mail is not set up yet, so nothing is sent and tests are off. <Link to="/settings?tab=email" viewTransition>Set it up in Settings</Link>
            </Callout>
          )}
          <div className="mail-templates">
            <nav className="mail-list" aria-label="E-mails">
              <select className="select mail-select" aria-label="E-mail" value={chosen.key} onChange={(e) => select(e.target.value)}>
                {templates.map((t) => <option key={t.key} value={t.key}>{t.name}{unsaved.has(t.key) ? " (unsaved)" : t.customized ? " (changed)" : ""}</option>)}
              </select>
              {templates.map((t) => (
                <button key={t.key} type="button" className="mail-item" aria-current={t.key === chosen.key || undefined} onClick={() => select(t.key)}>
                  <span className="t">
                    {t.name}
                    {unsaved.has(t.key) ? <span className="dot unsaved" title="Unsaved changes" /> : t.customized && <Tag>changed</Tag>}
                  </span>
                  <span className="d">{t.recipients}</span>
                </button>
              ))}
            </nav>
            <div className="stack" key={chosen.key}>
              <Editor template={chosen} draft={draft} mailReady={Boolean(data?.mailReady)} onChange={(text) => setDrafts((d) => ({ ...d, [chosen.key]: text }))} />
            </div>
            <Preview template={chosen} text={draft} />
          </div>
        </>
      )}
    </Page>
  );
}
