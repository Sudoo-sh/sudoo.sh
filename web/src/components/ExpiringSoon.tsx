import { CalendarClock, CalendarPlus } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { call, errorText } from "@/api/client";
import { keys, useCall, useInstancePermissions, useSettings } from "@/api/queries";
import type { Instance } from "@/api/types";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/expiry";
import { extendChoices, expiringSoon, extendedLifetime, stayedStopped, urgency } from "@/lib/expiry";
import { ago, fullDateTime } from "@/lib/format";
import { Button, Tag } from "./app/basics";
import { Menu, MenuItem, Modal, useToast } from "./app/overlay";
import { useCan } from "./Session";
import { StageTag } from "./StageTag";
import { Input } from "./ui/input";

/** Extend by some days: the new lifetime is the one it has plus the days (the panel refuses what the limits do not
 *  allow, and says why). */
export function Extend({ instance, choices }: { instance: Instance; choices: number[] }) {
  const t = useT(words);
  const c = useT(common).common;
  const toast = useToast();
  const [custom, setCustom] = useState(false);
  const [value, setValue] = useState("");
  const save = useCall(
    (add: number) =>
      call<Instance>("PATCH", `/instances/${instance.name}`, {
        delete_after_days: extendedLifetime(instance.settings.delete_after_days, add),
      }),
    [keys.instances, keys.instance(instance.name)],
  );
  const run = (add: number) =>
    save.mutate(add, {
      onSuccess: (answer) => {
        // an instance stopped at the end of its lifetime may have to stay stopped after the extension: say why, shown
        // longer (an error toast stays) and not as a plain success
        const stays = stayedStopped(answer);
        if (stays) toast("error", t.extended(instance.name, add), stays);
        else toast("success", t.extended(instance.name, add));
        setCustom(false);
      },
      onError: (e) => toast("error", t.notExtended, errorText(e)),
    });
  const days = Number(value);
  const valid = Number.isInteger(days) && days > 0;
  return (
    <>
      <Menu
        trigger={() => (
          <Button size="sm" icon={<CalendarPlus />} loading={save.isPending} aria-label={t.extendLabel(instance.name)}>
            {t.extend}
          </Button>
        )}
      >
        {() => (
          <>
            {choices.map((n) => (
              <MenuItem key={n} onSelect={() => run(n)}>
                {t.addDays(n)}
              </MenuItem>
            ))}
            <MenuItem onSelect={() => setCustom(true)}>{t.other}</MenuItem>
          </>
        )}
      </Menu>
      {custom && (
        <Modal
          title={t.customTitle(instance.name)}
          description={t.customText}
          onClose={() => setCustom(false)}
          footer={
            <>
              <Button onClick={() => setCustom(false)}>{c.cancel}</Button>
              <Button variant="primary" disabled={!valid} loading={save.isPending} onClick={() => run(days)}>
                {t.add}
              </Button>
            </>
          }
        >
          <Input
            type="number"
            inputMode="numeric"
            min={1}
            aria-label={t.days}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && valid && run(days)}
          />
        </Modal>
      )}
    </>
  );
}

/** The day choices of the Extend menu, as the settings and the role allow them */
export function useExtendChoices(): number[] {
  const { data: settings } = useSettings();
  const can = useCan();
  const setting = (key: string) => settings?.find((s) => s.key === key)?.value;
  return extendChoices(
    String(setting("expiry_extend_presets") ?? "1,7,30"),
    Number(setting("expiry_extend_max_days") ?? 30),
    can("instances.extend_unlimited"),
  );
}

function Row({ instance, at, choices }: { instance: Instance; at: number; choices: number[] }) {
  const t = useT(words);
  const roleCan = useCan();
  const { data: allowed } = useInstancePermissions(
    instance.name,
    JSON.stringify([instance.lock ?? null, instance.client_access ?? null]),
  );
  // the same permission the change needs, narrowed by the project's access and the branch's lock
  const mayExtend = roleCan("instances.operate") && (!allowed || allowed.includes("instances.operate"));
  const state = urgency(at);
  return (
    <li className="expiry-row" data-urgency={state}>
      <div className="expiry-name">
        <Link to={`/i/${instance.name}`} viewTransition className="truncate">
          {instance.name}
        </Link>
        <span className="sub truncate">
          {instance.project?.name ?? t.noProject}
          {instance.git.branch ? ` · ${instance.git.branch}` : ""}
        </span>
      </div>
      <StageTag stage={instance.stage} />
      <span className="expiry-when" title={fullDateTime(instance.expires_at)}>
        {state === "over" ? t.passed : t.left(ago(instance.expires_at))}
      </span>
      {mayExtend ? <Extend instance={instance} choices={choices} /> : <span />}
    </li>
  );
}

/** The home page's card of the instances that end soonest. Nothing is drawn when none ends in the window. */
export function ExpiringSoon({ instances }: { instances: Instance[] | undefined }) {
  const t = useT(words);
  const { data: settings } = useSettings();
  const can = useCan();
  const setting = (key: string) => settings?.find((s) => s.key === key)?.value;
  const days = Number(setting("expiry_card_days") ?? 7);
  const rows = Number(setting("expiry_card_rows") ?? 5);
  const { rows: list, total } = expiringSoon(instances, days, rows);
  if (!list.length) return null;
  const choices = extendChoices(
    String(setting("expiry_extend_presets") ?? "1,7,30"),
    Number(setting("expiry_extend_max_days") ?? 30),
    can("instances.extend_unlimited"),
  );
  return (
    <section className="card expiry-card rise" aria-labelledby="expiry-title">
      <div className="expiry-head">
        <CalendarClock aria-hidden />
        <div>
          <h2 id="expiry-title">{t.title}</h2>
          <p className="sub">{t.text(days)}</p>
        </div>
        <span className="spacer" />
        {total > list.length && (
          <Tag tone="accent">
            <Link to="/?filter=expiring">{t.seeAll(total)}</Link>
          </Tag>
        )}
      </div>
      <ul className="expiry-list">
        {list.map(({ instance, at }) => (
          <Row key={instance.name} instance={instance} at={at} choices={choices} />
        ))}
      </ul>
    </section>
  );
}
