// The demo's plan: a menu in the banner that switches the panel between the Free, Pro and Business plans (the
// in-browser backend then answers as a panel with that plan, premium.ts), and the mark on every premium page and
// section that says which plans include it.

import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Sparkles } from "lucide-react";
import { useState, type ReactNode } from "react";
import { PageMark } from "@/components/Shell";
import { Tag } from "@/components/app/basics";
import { Menu, useToast } from "@/components/app/overlay";
import {
  DropdownMenuCheckboxItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { useT } from "@/i18n";
import words from "@/i18n/demo";
import { setPremiumFrame, type PremiumPlace } from "@/premium/components";
import { currentPlan, hasPremiumFiles, PLANS, setPlan, type DemoPlan } from "./backend/premium";

/** The plan a visitor goes back to when they hide the Business features. */
let below: DemoPlan = currentPlan() === "business" ? "free" : currentPlan();

/** The plan menu of the banner (only when the demo was built with the premium features' files). */
export function DemoPlanMenu() {
  const t = useT(words);
  const client = useQueryClient();
  const toast = useToast();
  const [plan, setShown] = useState<DemoPlan>(currentPlan());
  if (!hasPremiumFiles()) return null;
  const choose = (next: DemoPlan) => {
    if (next === plan) return;
    if (next !== "business") below = next;
    setPlan(next);
    setShown(next);
    void client.invalidateQueries();
    toast("info", t.planNow(t.plans[next]!), t.planNowText);
  };
  // the menu stays open while the visitor tries the plans
  const stay = (event: Event) => event.preventDefault();
  return (
    <Menu
      trigger={() => (
        <button type="button" className="demo-plan" aria-label={`${t.plan}: ${t.plans[plan]}`}>
          <Sparkles aria-hidden="true" />
          <span className="demo-long">{t.plan}</span>
          <b>{t.plans[plan]}</b>
          <ChevronDown aria-hidden="true" />
        </button>
      )}
    >
      {() => (
        <>
          <DropdownMenuLabel>
            {t.planMenu}
            <span className="demo-plan-hint">{t.planMenuText}</span>
          </DropdownMenuLabel>
          <DropdownMenuCheckboxItem
            checked={plan === "business"}
            onSelect={stay}
            onCheckedChange={(on) => choose(on ? "business" : below)}
          >
            {t.showBusiness}
          </DropdownMenuCheckboxItem>
          <DropdownMenuSeparator />
          <DropdownMenuLabel>{t.plan}</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={plan} onValueChange={(value) => choose(value as DemoPlan)}>
            {PLANS.map((p) => (
              <DropdownMenuRadioItem key={p} value={p} onSelect={stay}>
                {t.plans[p]}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </>
      )}
    </Menu>
  );
}

/** Which plans include a premium feature, as a small tag. */
function PlanMark({ plans }: { plans: string }) {
  const t = useT(words);
  return (
    <span className="demo-mark" title={t.markHint}>
      <Tag tone="accent" icon={<Sparkles aria-hidden="true" />}>
        {t.mark(plans)}
      </Tag>
    </span>
  );
}

// sections that sit inside a row of buttons or above the page get no mark of their own
const INLINE = new Set(["instance.actions", "shell.banner"]);

function mark(node: ReactNode, { pkg, path, slot }: PremiumPlace): ReactNode {
  if (path) return <PageMark.Provider value={<PlanMark plans={pkg.plans} />}>{node}</PageMark.Provider>;
  if (!slot || INLINE.has(slot)) return node;
  return (
    <div className="demo-premium">
      <PlanMark plans={pkg.plans} />
      {node}
    </div>
  );
}

/** Mark every premium page and section with the plans that include it. */
export const markPremium = () => setPremiumFrame(mark);
