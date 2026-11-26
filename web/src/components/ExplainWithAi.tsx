// "Explain with AI": a button on a failed build, a preview of exactly what would be sent (nothing is sent before the
// person confirms), and the answer as plain text labelled as model-written (core: explain/service.py). The excerpt
// is log text, so only people who may read the branch's logs reach it (the server decides). Also the
// administrator's settings card.

import { useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import { useCall } from "@/api/queries";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import words from "@/i18n/explain";
import { useCan } from "./Session";
import { Button, Callout, Field, Switch } from "./app/basics";
import { Modal } from "./app/overlay";

type Availability = { available: boolean; reason: string | null; enabled_for_project: boolean };
type Preview = { id: string; host: string; model: string; payload: string; bytes: number; hidden: number };
type Answer = { text: string; model: string; host: string; generated: boolean };
export type ExplainSettings = {
  enabled: boolean;
  endpoint: string;
  model: string;
  key_set: boolean;
  projects: string[];
  destination_problem: string | null;
  limits: { per_user_hour: number; per_project_hour: number };
};

const base = (project: string, build: string) =>
  `/projects/${encodeURIComponent(project)}/builds/${encodeURIComponent(build)}/explain`;

export function ExplainWithAi({ project, build, lang }: { project: string; build: string; lang: "en" | "ar" }) {
  const t = useT(words);
  const can = useCan();
  const [step, setStep] = useState<"idle" | "preview" | "answer">("idle");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const state = useQuery({
    queryKey: ["explain", project, build],
    queryFn: () => api.get<Availability>(base(project, build)),
    retry: false,
  });
  const prepare = useCall(() => api.post<Preview>(`${base(project, build)}/preview`, { lang }), []);
  const send = useCall((id: string) => api.post<Answer>(`${base(project, build)}/confirm`, { preview: id }), []);
  const option = useCall(
    (enabled: boolean) => api.put(`/projects/${encodeURIComponent(project)}/explain`, { enabled }),
    [["explain", project, build]],
  );
  const found = state.data;
  if (!found || state.error) return null;
  // nothing is offered to people who cannot use it, unless the only thing missing is this project's switch
  if (!found.available) {
    if (found.reason === "project_off" && can("projects.manage"))
      return (
        <div className="row-wrap small">
          <span className="muted">{t.reasons.project_off}</span>
          <Button size="sm" disabled={option.isPending} onClick={() => option.mutate(true)}>
            {t.turnOn}
          </Button>
        </div>
      );
    return null;
  }
  const close = () => {
    setStep("idle");
    setPreview(null);
    setAnswer(null);
    prepare.reset();
    send.reset();
  };
  return (
    <div className="explain-ai stack-sm">
      <div className="row-wrap">
        <Button
          size="sm"
          icon={<Sparkles />}
          loading={prepare.isPending}
          onClick={() =>
            prepare.mutate(undefined, {
              onSuccess: (made) => {
                setPreview(made);
                setStep("preview");
              },
            })
          }
        >
          {t.button}
        </Button>
        {can("projects.manage") && (
          <Button size="sm" variant="ghost" disabled={option.isPending} onClick={() => option.mutate(false)}>
            {t.turnOff}
          </Button>
        )}
      </div>
      {prepare.error && <Callout tone="danger">{errorText(prepare.error)}</Callout>}
      {step === "preview" && preview && (
        <Modal
          wide
          title={t.previewTitle}
          description={t.previewText(preview.host, preview.model)}
          onClose={close}
          footer={
            <>
              <Button variant="ghost" onClick={close}>
                {t.cancel}
              </Button>
              <Button
                variant="primary"
                loading={send.isPending}
                onClick={() =>
                  send.mutate(preview.id, {
                    onSuccess: (done) => {
                      setAnswer(done);
                      setStep("answer");
                    },
                  })
                }
              >
                {send.isPending ? t.sending : t.confirm}
              </Button>
            </>
          }
        >
          <div className="stack-sm">
            <p className="small">{t.privacy}</p>
            <pre className="why-lines" dir="ltr" tabIndex={0} data-testid="explain-payload">
              {preview.payload || t.noPayload}
            </pre>
            <p className="xsmall faint">
              {t.hidden(preview.hidden)} {t.size(preview.bytes)}. {t.expires}
            </p>
            {send.error && <Callout tone="danger">{errorText(send.error)}</Callout>}
          </div>
        </Modal>
      )}
      {step === "answer" && answer && (
        <Modal
          wide
          title={t.answerTitle}
          onClose={close}
          footer={
            <Button variant="primary" onClick={close}>
              {t.close}
            </Button>
          }
        >
          <div className="stack-sm">
            <Callout tone="warning">{t.generated(answer.model)}</Callout>
            {/* text only: the model's words are never turned into links, markup or actions */}
            <div className="small" dir="auto" style={{ whiteSpace: "pre-wrap" }} data-testid="explain-answer">
              {answer.text}
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

/** The administrator's settings of AI explanations. The key is only ever written here, never read back. */
export function ExplainSettingsFields() {
  const t = useT(words).settings;
  const q = useQuery({ queryKey: ["explain-settings"], queryFn: () => api.get<ExplainSettings>("/explain") });
  const [draft, setDraft] = useState<{ enabled?: boolean; endpoint?: string; model?: string; key?: string }>({});
  const save = useCall((body: object) => api.put<ExplainSettings>("/explain", body), [["explain-settings"]]);
  const found = q.data;
  if (!found) return q.error ? <Callout tone="danger">{errorText(q.error)}</Callout> : null;
  const value = { enabled: found.enabled, endpoint: found.endpoint, model: found.model, ...draft };
  return (
    <div className="stack-sm">
      <Switch checked={value.enabled} onChange={(enabled) => setDraft((d) => ({ ...d, enabled }))} label={t.enabled} />
      <Field label={t.endpoint} hint={t.endpointHint} error={found.destination_problem ? t.problem : undefined}>
        <Input
          className="mono"
          dir="ltr"
          value={value.endpoint}
          onChange={(e) => setDraft((d) => ({ ...d, endpoint: e.target.value }))}
        />
      </Field>
      <Field label={t.model}>
        <Input
          className="mono"
          dir="ltr"
          value={value.model}
          onChange={(e) => setDraft((d) => ({ ...d, model: e.target.value }))}
        />
      </Field>
      <Field label={t.key} hint={`${found.key_set ? t.keySet : t.keyNone} ${t.keyHint}`}>
        <Input
          className="mono"
          dir="ltr"
          type="password"
          autoComplete="off"
          value={draft.key ?? ""}
          onChange={(e) => setDraft((d) => ({ ...d, key: e.target.value }))}
        />
      </Field>
      <p className="xsmall muted">{t.limits(found.limits.per_user_hour, found.limits.per_project_hour)}</p>
      <p className="xsmall muted">{t.projects(found.projects.length)}</p>
      {save.error && <Callout tone="danger">{errorText(save.error)}</Callout>}
      <div className="row-wrap">
        <Button
          variant="primary"
          loading={save.isPending}
          disabled={Object.keys(draft).length === 0}
          onClick={() => save.mutate(draft, { onSuccess: () => setDraft({}) })}
        >
          {t.save}
        </Button>
        {save.isSuccess && Object.keys(draft).length === 0 && <span className="small muted">{t.saved}</span>}
      </div>
    </div>
  );
}
