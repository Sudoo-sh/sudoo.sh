import { ArrowRight, ArrowUpCircle, ClipboardCheck, FlaskConical, History, Rocket, Undo2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useCall, useUpgrade } from "@/api/queries";
import type { Instance, Job, Upgrade, UpgradeModuleStatus } from "@/api/types";
import { Button, Callout, Empty, Field, Skeleton, Tag } from "@/components/app/basics";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { SelectBox } from "@/components/app/select";
import { Modal, useToast } from "@/components/app/overlay";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/upgrades";
import { ago, dateTime } from "@/lib/format";

const STATUS_TONE: Record<UpgradeModuleStatus, "positive" | "caution" | "negative" | undefined> = {
  ready: "positive",
  version_mismatch: "negative",
  unknown_version: "caution",
  not_installable: "negative",
  missing: "negative",
};

type Dialog = null | "test" | "promote" | "cancel";

/** The Upgrade tab: the instance's database moved to a newer Odoo version (pre-check, test, production). */
export function UpgradeTab({ instance, onJob }: { instance: Instance; onJob: (id: string) => void }) {
  const { data } = useUpgrade(instance.name);
  if (!data) return <Skeleton height={320} />;
  return <UpgradeView instance={instance} data={data} onJob={onJob} />;
}

function UpgradeView({ instance, data, onJob }: { instance: Instance; data: Upgrade; onJob: (id: string) => void }) {
  const t = useT(words);
  const toast = useToast();
  const [chosen, setChosen] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const target = chosen ?? (data.target && data.targets.includes(data.target) ? data.target : data.next);
  const refresh = [
    [...keys.instance(instance.name), "upgrade"],
    keys.instance(instance.name),
    keys.jobs(instance.name),
  ] as const;
  const call = useCall(
    async ({ path, body, title }: { path: string; body?: Record<string, unknown>; title: string }) => {
      try {
        const answer = await api.post<Job | Upgrade>(`/instances/${instance.name}/upgrade/${path}`, body ?? {});
        if (answer && "status" in answer && "action" in answer) {
          toast("success", `${t.started}: ${title}`);
          onJob(answer.id);
        }
        return answer;
      } catch (error) {
        toast("error", path === "check" ? t.checkFailed : `${t.notStarted}: ${title}`, errorText(error));
        throw error;
      }
    },
    refresh,
  );

  if (!target && !data.history.length) {
    return (
      <section className="card pad">
        <Empty icon={<ArrowUpCircle />} title={t.newest}>
          {t.newestText}
        </Empty>
      </section>
    );
  }
  const busy = !!instance.current_job || call.isPending || data.state === "testing" || data.state === "promoting";
  const report = data.report && data.report.target === target ? data.report : null;
  const tested = !!data.tested_at && ["tested", "promotion_failed", "rolled_back"].includes(data.state ?? "");
  const blocked = !data.allowed || !!data.refusal;

  return (
    <div className="stack">
      {!target && (
        <section className="card pad">
          <Empty icon={<ArrowUpCircle />} title={t.newest}>
            {t.newestText}
          </Empty>
        </section>
      )}
      {target && (
        <section className="card">
          <div className="card-head">
            <ArrowUpCircle />
            <div className="spacer">
              <h2>{t.title(target)}</h2>
              <div className="sub">{t.intro}</div>
            </div>
            {data.state && (
              <Tag
                tone={
                  data.state === "promoted" || data.state === "tested"
                    ? "positive"
                    : data.state.endsWith("failed")
                      ? "negative"
                      : undefined
                }
              >
                {t.states[data.state]}
              </Tag>
            )}
          </div>
          <div className="card-body stack">
            <div className="row-wrap" style={{ gap: 8 }}>
              <Tag mono>{data.from_version}</Tag>
              <ArrowRight className="flip-rtl" size={16} strokeWidth={1.5} />
              {data.targets.length > 1 ? (
                <SelectBox
                  dir="ltr"
                  aria-label={t.targetLabel}
                  value={target ?? ""}
                  onValueChange={(value) => setChosen(value)}
                  disabled={busy}
                >
                  {data.targets.map((v) => (
                    <option key={v}>{v}</option>
                  ))}
                </SelectBox>
              ) : (
                <Tag mono tone="accent">
                  {target}
                </Tag>
              )}
              <Tag>{t.methods[data.method]}</Tag>
            </div>
            <p className="small muted">
              {t.methodText[data.method]}
              {data.skips_versions && data.targets.length > 1 ? ` ${t.skips}` : ""}
            </p>
            {!data.allowed && <Callout tone="warning">{t.notAllowed}</Callout>}
            {data.refusal && data.allowed && <Callout tone="warning">{data.refusal}</Callout>}
          </div>
        </section>
      )}

      {data.state === "promoted" && data.promotion && (
        <section className="card">
          <div className="card-head">
            <Rocket />
            <div className="spacer">
              <h2>
                {t.promoted(
                  data.promotion.target ?? data.target ?? "",
                  ago(data.promotion.finished_at ?? data.promotion.at ?? null),
                )}
              </h2>
              {data.rollback_open && data.promotion.rollback_until && (
                <div className="sub">{t.windowOpen(dateTime(data.promotion.rollback_until))}</div>
              )}
            </div>
          </div>
          {data.rollback_open && (
            <div className="card-foot row-wrap">
              <Button variant="danger" icon={<Undo2 />} disabled={busy} onClick={() => setDialog("cancel")}>
                {t.cancel}
              </Button>
              <Button
                disabled={busy}
                onClick={() =>
                  call.mutate({ path: "keep", title: t.keep }, { onSuccess: () => toast("success", t.kept) })
                }
              >
                {t.keep}
              </Button>
            </div>
          )}
        </section>
      )}

      {target && (
        <>
          <section className="card">
            <div className="card-head">
              <ClipboardCheck />
              <div className="spacer">
                <h2>{t.step1}</h2>
                <div className="sub">{t.step1Text}</div>
              </div>
              <Button
                disabled={busy || blocked || !target}
                loading={call.isPending && call.variables?.path === "check"}
                onClick={() => call.mutate({ path: "check", body: { target }, title: t.runCheck })}
              >
                {report ? t.checkAgain : t.runCheck}
              </Button>
            </div>
            {report && (
              <div className="card-body stack">
                <div className="row-wrap small muted" style={{ gap: 12 }}>
                  <span>{t.checked(ago(report.checked_at))}</span>
                  <span>{report.installed_count ? t.installed(report.installed_count) : t.installedUnknown}</span>
                </div>
                {report.blocking.map((b) => (
                  <Callout key={b.code} tone="danger">
                    {b.text}
                  </Callout>
                ))}
                {report.warnings
                  .filter((w) => !["modules_not_ready", "no_branch"].includes(w.code))
                  .map((w) => (
                    <Callout key={w.code} tone="warning">
                      {w.text}
                    </Callout>
                  ))}
                {!report.repository ? (
                  <p className="small">{t.noRepository}</p>
                ) : report.branch_exists ? (
                  <p className="small">
                    {t.branchFound(
                      <b key="b" className="mono" dir="ltr">
                        {report.branch}
                      </b>,
                    )}
                  </p>
                ) : (
                  report.branch_exists === false && (
                    <Callout tone="warning">
                      {t.branchMissing(
                        <b key="b" className="mono" dir="ltr">
                          {report.branch}
                        </b>,
                      )}
                    </Callout>
                  )
                )}
                {report.repository && (
                  <>
                    <h3 className="small">{t.modulesTitle}</h3>
                    {report.modules.length === 0 ? (
                      <p className="small muted">{t.noModules}</p>
                    ) : (
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>{t.module}</TableHead>
                            <TableHead>{t.current}</TableHead>
                            <TableHead>{t.inBranch}</TableHead>
                            <TableHead>{t.status}</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {report.modules.map((m) => (
                            <TableRow key={m.name}>
                              <TableCell className="mono small" dir="ltr">
                                {m.name}
                              </TableCell>
                              <TableCell className="mono small" dir="ltr">
                                {m.version || "–"}
                              </TableCell>
                              <TableCell className="mono small" dir="ltr">
                                {m.target_version || "–"}
                              </TableCell>
                              <TableCell>
                                <Tag tone={STATUS_TONE[m.status]}>{t.moduleStatus[m.status]}</Tag>
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    )}
                    <p className="small muted">{t.portNote}</p>
                  </>
                )}
              </div>
            )}
          </section>

          <section className="card">
            <div className="card-head">
              <FlaskConical />
              <div className="spacer">
                <h2>{t.step2}</h2>
                <div className="sub">{t.step2Text}</div>
              </div>
              <Button
                variant={tested ? "default" : "primary"}
                disabled={busy || blocked || !report || report.blocking.length > 0}
                onClick={() => setDialog("test")}
              >
                {data.staging && data.tested_at ? t.testAgain : t.test}
              </Button>
            </div>
            <div className="card-body stack">
              {!report && <p className="small muted">{t.testFirst}</p>}
              {data.staging && data.tested_at && (
                <p className="small">
                  {t.testedOn(
                    <Link key="s" to={`/i/${data.staging}`} viewTransition>
                      <bdi>{data.staging}</bdi>
                    </Link>,
                    ago(data.tested_at),
                  )}
                </p>
              )}
              {data.staging && <p className="small muted">{t.pushNote}</p>}
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <Rocket />
              <div className="spacer">
                <h2>{instance.stage === "production" ? t.step3 : t.step3Other}</h2>
                <div className="sub">{t.step3Text}</div>
              </div>
              <Button variant="danger" disabled={busy || blocked || !tested} onClick={() => setDialog("promote")}>
                {instance.stage === "production" ? t.promote : t.promoteOther}
              </Button>
            </div>
            {!tested && (
              <div className="card-body">
                <p className="small muted">{t.promoteFirst}</p>
              </div>
            )}
          </section>
        </>
      )}

      <section className="card">
        <div className="card-head">
          <History />
          <div className="spacer">
            <h2>{t.history}</h2>
          </div>
        </div>
        <div className="card-body">
          {data.history.length === 0 ? (
            <p className="small muted">{t.noHistory}</p>
          ) : (
            <ul className="stack-sm small" style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {data.history.map((h, i) => (
                <li key={`${h.at}-${i}`}>
                  <span className="faint">{dateTime(h.at)}</span> · <b>{h.event}</b>{" "}
                  <span className="muted">{h.detail}</span>{" "}
                  <span className="faint">
                    · <bdi>{h.user}</bdi>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {(dialog === "test" || dialog === "promote") && target && (
        <RunDialog
          instance={instance}
          data={data}
          target={target}
          kind={dialog}
          onClose={() => setDialog(null)}
          onRun={(body) =>
            call.mutate({ path: dialog, body: { ...body, target }, title: dialog === "test" ? t.test : t.promote })
          }
        />
      )}
      {dialog === "cancel" && (
        <CancelDialog
          instance={instance}
          onClose={() => setDialog(null)}
          onRun={() =>
            call.mutate({
              path: "cancel",
              body: { confirm_production: instance.stage === "production" },
              title: t.cancel,
            })
          }
        />
      )}
    </div>
  );
}

/** Asks for what a test or the production upgrade needs: the subscription code and the terms (Enterprise). */
function RunDialog({
  instance,
  data,
  target,
  kind,
  onClose,
  onRun,
}: {
  instance: Instance;
  data: Upgrade;
  target: string;
  kind: "test" | "promote";
  onClose: () => void;
  onRun: (body: Record<string, unknown>) => void;
}) {
  const t = useT(words);
  const c = useT(common).common;
  const [code, setCode] = useState("");
  const [accepted, setAccepted] = useState(false);
  const enterprise = data.method === "enterprise";
  const ready = !enterprise || (accepted && code.trim().length >= 4);
  const production = instance.stage === "production";
  const name = (
    <bdi key="n">
      <b>{instance.name}</b>
    </bdi>
  );
  return (
    <Modal
      title={kind === "test" ? t.dialogTest(target) : t.dialogPromote(target)}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{c.cancel}</Button>
          <Button
            variant={kind === "promote" ? "danger" : "primary"}
            disabled={!ready}
            onClick={() => {
              onRun({
                ...(enterprise ? { subscription_code: code.trim(), accept_odoo_terms: accepted } : {}),
                ...(kind === "promote" ? { confirm_production: production } : {}),
              });
              onClose();
            }}
          >
            {kind === "promote" ? t.confirmUpgrade : t.start}
          </Button>
        </>
      }
    >
      <div className="stack">
        {kind === "promote" && production && (
          <Callout tone="warning">
            <b>{t.production}</b> {t.promoteSteps(name)}
          </Callout>
        )}
        {kind === "test" && <p className="small">{t.testSteps(name)}</p>}
        {enterprise ? (
          <>
            <Field label={t.code} hint={t.codeHint}>
              <Input
                className="mono"
                type="password"
                autoComplete="off"
                spellCheck={false}
                dir="ltr"
                maxLength={100}
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
            </Field>
            <label className="licence-check">
              <Checkbox checked={accepted} onCheckedChange={(value) => setAccepted(value === true)} />
              <span>
                {t.terms(
                  <a key="l" href={data.terms_url ?? "#"} target="_blank" rel="noreferrer">
                    {t.termsLink}
                  </a>,
                )}{" "}
                {t.accept}
              </span>
            </label>
          </>
        ) : (
          <p className="small muted">
            {t.openupgradeNote(
              <a key="l" href={data.openupgrade_source ?? "#"} target="_blank" rel="noreferrer" dir="ltr">
                {data.openupgrade_source?.replace(/^https:\/\//, "")}
              </a>,
            )}
          </p>
        )}
      </div>
    </Modal>
  );
}

function CancelDialog({ instance, onClose, onRun }: { instance: Instance; onClose: () => void; onRun: () => void }) {
  const t = useT(words);
  const c = useT(common).common;
  return (
    <Modal
      title={t.dialogCancel}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{c.cancel}</Button>
          <Button
            variant="danger"
            icon={<Undo2 />}
            onClick={() => {
              onRun();
              onClose();
            }}
          >
            {t.cancel}
          </Button>
        </>
      }
    >
      <Callout tone="warning">
        {instance.stage === "production" && (
          <>
            <b>{t.production}</b>{" "}
          </>
        )}
        {t.cancelText(
          <bdi key="n">
            <b>{instance.name}</b>
          </bdi>,
        )}
      </Callout>
    </Modal>
  );
}
