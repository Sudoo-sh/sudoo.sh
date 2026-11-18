// Settings › Plan: the panel's Sudoo.sh plan from the hub's signed licence (src/sudoo/entitlements.py): the plan, what
// it allows, what the panel uses, withheld production, the branches that leave production after a downgrade, and
// where to manage it (the account site, the pricing page). The panel never changes the plan itself.

import { ArrowUpRight, BadgeCheck, CircleSlash, Gauge, Layers } from "lucide-react";
import { Link } from "react-router";
import { useSudooPlan } from "@/api/queries";
import type { PlanStatus } from "@/api/types";
import { useT } from "@/i18n";
import words from "@/i18n/plan";
import { ago, dateTime } from "@/lib/format";
import { useMe } from "./Session";
import { Callout, Skeleton, Tag } from "./ui/basics";

const FEATURES = ["per_project_access", "custom_domains", "offsite_backups"] as const;

function External({ href, children, primary }: { href: string; children: string; primary?: boolean }) {
  return (
    <a className={`btn sm ${primary ? "primary" : ""}`} href={href} target="_blank" rel="noreferrer">
      {children} <ArrowUpRight className="flip-rtl" />
    </a>
  );
}

/** What stops new production, said once: withheld, banned, expired; and the branches leaving production. */
export function PlanNotices({ plan }: { plan: PlanStatus }) {
  const t = useT(words);
  return (
    <>
      {plan.banned && <Callout tone="danger">{t.banned}</Callout>}
      {plan.production_withheld && (
        <Callout tone="warning">
          <b>{t.withheld}.</b> {t.withheldReasons[plan.production_withheld] ?? plan.production_withheld} {t.withheldKeep}{" "}
          <a href={plan.links.account} target="_blank" rel="noreferrer">{t.managePlan}</a>
        </Callout>
      )}
      {plan.source === "expired" && <Callout tone="warning">{t.expiredBanner}</Callout>}
      {plan.over_limit.length > 0 && (
        <Callout tone="warning">
          {t.overLimit}{" "}
          {plan.leaving_production.length ? (
            <>{t.leaving} <b dir="ltr">{plan.leaving_production.join(", ")}</b>.</>
          ) : (
            t.leavingNone
          )}{" "}
          {plan.production_keep && <span className="faint">{t.kept(plan.production_keep.join(", ") || "—")}.</span>}{" "}
          <a href={plan.links.keep} target="_blank" rel="noreferrer">{t.chooseKept}</a>
        </Callout>
      )}
    </>
  );
}

/** The banner of the panel's frame for super admins: not linked (the Free plan), or the plan needs attention. */
export function PlanBanner() {
  const me = useMe();
  const t = useT(words);
  const allowed = me.permissions.includes("settings.manage");
  const { data: plan } = useSudooPlan();
  if (!allowed || !plan) return null;
  if (!plan.linked)
    return (
      <div className="notices" role="status">
        <Callout tone="info">
          {t.unlinkedBanner(plan.entitlements.max_production_branches ?? 1)}{" "}
          <Link to="/settings?tab=marketplace" viewTransition>{t.unlinkedAction}</Link>
        </Callout>
      </div>
    );
  if (plan.leaving_production.length)
    return (
      <div className="notices" role="status">
        <Callout tone="warning">
          {t.leavingBanner(plan.leaving_production.join(", "))}{" "}
          <a href={plan.links.keep} target="_blank" rel="noreferrer">{t.chooseKept}</a>
        </Callout>
      </div>
    );
  if (plan.source === "expired" || plan.production_withheld)
    return (
      <div className="notices" role="status">
        <Callout tone="warning">
          {plan.production_withheld ? `${t.withheld}. ${t.withheldKeep}` : t.expiredBanner}{" "}
          <Link to="/settings?tab=plan" viewTransition>{t.title}</Link>
        </Callout>
      </div>
    );
  return null;
}

export function PlanSettings() {
  const t = useT(words);
  const { data: plan, isPending } = useSudooPlan();
  if (isPending || !plan) return <Skeleton height={300} />;
  const e = plan.entitlements;
  const limit = (n: number | null) => (n === null ? t.unlimited : String(n));
  const tone = plan.source === "hub" ? "positive" : plan.source === "expired" ? "negative" : "caution";
  return (
    <div className="stack">
      <section className="card">
        <div className="card-head">
          <BadgeCheck />
          <div className="spacer">
            <h2>{t.plan(plan.plan.charAt(0).toUpperCase() + plan.plan.slice(1))}</h2>
            <div className="sub">{t.text}</div>
          </div>
          <div className="row-wrap">
            {plan.linked ? <External href={plan.links.account} primary>{t.managePlan}</External> : <Link className="btn sm primary" to="/settings?tab=marketplace">{t.linkPanel}</Link>}
            <External href={plan.links.pricing}>{t.comparePlans}</External>
          </div>
        </div>
        <div className="card-body stack">
          <div className="row-wrap small">
            <Tag tone={tone}>{t.sources[plan.source]}</Tag>
            {plan.account_name && <span className="muted">{t.account(plan.account_name)}</span>}
            {plan.received_at && <span className="faint">{t.heardAt(ago(plan.received_at))}</span>}
            {plan.valid_until && <span className="faint">{t.validUntil(dateTime(plan.valid_until))}</span>}
          </div>
          <PlanNotices plan={plan} />
        </div>
      </section>
      <div className="grid-2">
        <section className="card">
          <div className="card-head"><Gauge /><h2>{t.entitlements}</h2></div>
          <div className="card-body">
            <dl className="dl">
              <dt>{t.productionBranches}</dt><dd>{t.of(plan.usage.production_branches, limit(e.max_production_branches))}</dd>
              <dt>{t.servers}</dt><dd>{t.of(plan.usage.servers, limit(e.max_servers))}</dd>
              <dt>{t.projects}</dt><dd>{t.of(plan.usage.projects, limit(e.max_projects))}</dd>
              <dt>{t.support}</dt><dd>{t.supportLevels[e.support_level] ?? e.support_level}</dd>
              {e.period && <><dt>{t.period}</dt><dd>{t.periods[e.period] ?? e.period}</dd></>}
              {e.renews_at && <><dt>{t.renewal}</dt><dd>{dateTime(typeof e.renews_at === "number" ? new Date(e.renews_at * 1000).toISOString() : e.renews_at)}</dd></>}
              {e.trial_plan && <><dt>{t.trialLabel}</dt><dd>{e.trial_plan}</dd></>}
              {Object.keys(e.addons).length > 0 && (
                <><dt>{t.addons}</dt><dd>{Object.entries(e.addons).map(([code, n]) => t.addon(code, n)).join(", ")}</dd></>
              )}
            </dl>
          </div>
        </section>
        <section className="card">
          <div className="card-head"><Layers /><h2>{t.features}</h2></div>
          <div className="card-body">
            <dl className="dl">
              <dt>{t.feature.teams}</dt>
              <dd>{e.teams ? <Tag tone="positive">{t.included}</Tag> : <span className="faint row"><CircleSlash size={14} /> {t.notIncluded}</span>}</dd>
              {FEATURES.map((f) => (
                <span key={f} style={{ display: "contents" }}>
                  <dt>{t.feature[f]}</dt>
                  <dd>{e.features[f] ? <Tag tone="positive">{t.included}</Tag> : <span className="faint row"><CircleSlash size={14} /> {t.notIncluded}</span>}</dd>
                </span>
              ))}
            </dl>
          </div>
        </section>
      </div>
      <section className="card">
        <div className="card-head"><Layers /><h2>{t.usage}</h2></div>
        <div className="card-body stack-sm">
          <div className="row-wrap small">
            <span className="muted">{t.instancesByStage}:</span>
            {(["production", "staging", "development"] as const).map((s) => (
              <Tag key={s}>{t.stages[s]} {plan.usage.instances_by_stage[s] ?? 0}</Tag>
            ))}
          </div>
          <div className="small muted">{t.branchNames}</div>
          {plan.usage.production_branch_names.length ? (
            <div className="row-wrap">
              {plan.usage.production_branch_names.map((n) => (
                <Tag key={n} mono tone={plan.leaving_production.includes(n) ? "caution" : undefined}>{n}</Tag>
              ))}
            </div>
          ) : (
            <p className="small faint">{t.noProduction}</p>
          )}
          {plan.production_refusal && (
            <Callout tone="info">{t.refusal} {plan.production_refusal}</Callout>
          )}
        </div>
      </section>
    </div>
  );
}

/** A paid feature the plan lacks: what it is and where to get it (the server refuses its use anyway). */
export function FeatureUpsell({ feature }: { feature: "teams" | "per_project_access" | "custom_domains" | "offsite_backups" }) {
  const t = useT(words);
  const { data: plan } = useSudooPlan();
  if (!plan) return null;
  const has = feature === "teams" ? plan.entitlements.teams : plan.entitlements.features[feature];
  if (has) return null;
  const name = t.feature[feature];
  return (
    <Callout tone="info">
      {feature === "teams" || feature === "custom_domains" || feature === "offsite_backups" ? t.upsell(name) : t.upsellOne(name)}{" "}
      <a href={plan.links.pricing} target="_blank" rel="noreferrer">{t.comparePlans}</a>
      {plan.linked && <> · <a href={plan.links.account} target="_blank" rel="noreferrer">{t.managePlan}</a></>}
    </Callout>
  );
}
