// An instance's Shell tab: choose what to open (a terminal in the Odoo container, odoo shell, psql; as root when the
// panel allows it), confirm it on a production instance every time, then the terminal itself (ShellTerminal). Super
// admins also see the shells opened here, end open ones and play recordings. On a phone, a notice first: a terminal
// needs a keyboard.

import { Play, Square, SquareTerminal } from "lucide-react";
import { useState, useSyncExternalStore } from "react";
import { api } from "@/api/client";
import { useCall, useShellOptions, useShells } from "@/api/queries";
import type { Instance, ShellSession, ShellTarget } from "@/api/types";
import { useCan } from "@/components/Shell";
import { Button, Callout, Segmented, Skeleton, Switch, Tag } from "@/components/app/basics";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Modal } from "@/components/app/overlay";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/shell";
import { dateTime, duration, elapsed } from "@/lib/format";
import { ShellPlayer, ShellTerminal } from "./ShellTerminal";

const TARGETS: ShellTarget[] = ["shell", "odoo-shell", "psql"];
const PHONE = "(max-width: 767px) and (pointer: coarse)";

function usePhone() {
  return useSyncExternalStore(
    (changed) => {
      const media = matchMedia(PHONE);
      media.addEventListener("change", changed);
      return () => media.removeEventListener("change", changed);
    },
    () => matchMedia(PHONE).matches,
  );
}

type Opened = { key: number; target: ShellTarget; asRoot: boolean; confirmed: boolean };

export function ShellTab({ instance }: { instance: Instance }) {
  const t = useT(words);
  const c = useT(common).common;
  const can = useCan();
  const { data: options, error } = useShellOptions(instance.name);
  const phone = usePhone();
  const [target, setTarget] = useState<ShellTarget>("shell");
  const [asRoot, setAsRoot] = useState(false);
  const [opened, setOpened] = useState<Opened | null>(null);
  const [asking, setAsking] = useState<Omit<Opened, "key" | "confirmed"> | null>(null);
  const [anyway, setAnyway] = useState(false);

  if (error) return <Callout tone="danger">{String((error as Error).message)}</Callout>;
  if (!options)
    return (
      <div className="card card-body">
        <Skeleton height={160} />
      </div>
    );

  const allowed = TARGETS.filter((name) => options.targets[name].allowed);
  const chosen = allowed.includes(target) ? target : allowed[0];
  const choice = chosen ? options.targets[chosen] : null;
  const rootAllowed = chosen === "shell" && options.root.allowed;
  const recordedFor = (what: { target: ShellTarget; asRoot: boolean }) => {
    const found = what.asRoot ? options.root : options.targets[what.target];
    return found.allowed && found.recorded;
  };
  const recorded = choice?.allowed && recordedFor({ target: chosen!, asRoot: rootAllowed && asRoot });

  /** open (or open again): on production, after a confirmation, every time */
  const open = (what: Omit<Opened, "key" | "confirmed">, confirmed = false) => {
    if (options.production && !confirmed) {
      setAsking(what);
      return;
    }
    setOpened((o) => ({ key: (o?.key ?? 0) + 1, ...what, confirmed }));
  };

  let body;
  if (!options.enabled) body = <Callout>{t.disabled}</Callout>;
  else if (!chosen) {
    const reason = Object.values(options.targets).find((x) => !x.allowed && x.code !== "permission");
    body = <Callout>{reason && !reason.allowed ? reason.reason : t.noneAllowed}</Callout>;
  } else if (opened) {
    body = (
      <ShellTerminal
        key={opened.key}
        instance={instance.name}
        stage={instance.stage}
        showUser={can("shells.supervise")}
        target={opened.target}
        asRoot={opened.asRoot}
        confirmed={opened.confirmed}
        onReconnect={() => open({ target: opened.target, asRoot: opened.asRoot })}
        onClose={() => setOpened(null)}
      />
    );
  } else {
    body = (
      <div className="card">
        <div className="card-head">
          <h2>{t.targets[chosen]}</h2>
          {options.production && <Tag tone="caution">{t.production}</Tag>}
          {recorded && (
            <span title={t.recordedHint}>
              <Tag tone="accent">{t.recorded}</Tag>
            </span>
          )}
        </div>
        <div className="card-body shell-start">
          {allowed.length > 1 && (
            <Segmented
              value={chosen}
              onChange={setTarget}
              options={allowed.map((name) => ({ value: name, label: <span dir="ltr">{t.targets[name]}</span> }))}
            />
          )}
          <p className="muted">{t.targetHints[chosen]}</p>
          {rootAllowed && (
            <div className="shell-root">
              <Switch checked={asRoot} onChange={setAsRoot} label={t.asRoot} />
              <span className="faint small">{t.asRootHint}</span>
            </div>
          )}
          {!options.running && <Callout tone="warning">{t.notRunning}</Callout>}
          {phone && !anyway ? (
            <div className="shell-phone">
              <Callout>
                <b>{t.phoneTitle}</b> {t.phoneText}
              </Callout>
              <Button size="sm" icon={<SquareTerminal />} disabled={!options.running} onClick={() => setAnyway(true)}>
                {t.openAnyway}
              </Button>
            </div>
          ) : (
            <div className="row-wrap shell-open">
              <Button
                variant="primary"
                icon={<SquareTerminal />}
                disabled={!options.running}
                onClick={() => open({ target: chosen, asRoot: rootAllowed && asRoot })}
              >
                {t.open}
              </Button>
              <span className="faint small">{t.limits(options.limits.idle_minutes, options.limits.max_minutes)}</span>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="shell-tab">
      {body}
      {can("shells.supervise") && <ShellSessions instance={instance.name} />}
      {asking && (
        <Modal
          title={t.confirmTitle}
          description={
            <>
              {t.confirmText(
                <b key="n">
                  <bdi>{instance.name}</bdi>
                </b>,
              )}{" "}
              {recordedFor(asking) ? t.confirmRecorded : ""}
            </>
          }
          onClose={() => setAsking(null)}
          footer={
            <>
              <Button onClick={() => setAsking(null)}>{c.cancel}</Button>
              <Button
                variant="danger"
                icon={<SquareTerminal />}
                onClick={() => {
                  open(asking, true);
                  setAsking(null);
                }}
              >
                {t.confirm}
              </Button>
            </>
          }
        />
      )}
    </div>
  );
}

function ShellSessions({ instance }: { instance: string }) {
  const t = useT(words);
  const { data } = useShells(instance, true);
  const [playing, setPlaying] = useState<ShellSession | null>(null);
  const end = useCall((id: string) => api.del(`/shells/${id}`), [["shells", instance]]);
  return (
    <div className="card">
      <div className="card-head">
        <h2>{t.sessionsTitle}</h2>
        <span className="sub">{t.sessionsSub}</span>
      </div>
      {!data ? (
        <div className="card-body">
          <Skeleton height={80} />
        </div>
      ) : !data.length ? (
        <div className="card-body faint">{t.noSessions}</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t.who}</TableHead>
              <TableHead>{t.what}</TableHead>
              <TableHead>{t.started}</TableHead>
              <TableHead>{t.length}</TableHead>
              <TableHead>{t.ending}</TableHead>
              <TableHead aria-label={t.play} />
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((s) => (
              <TableRow key={s.id}>
                <TableCell>
                  <bdi>{s.user}</bdi>
                </TableCell>
                <TableCell>
                  <span className="small">{t.asUser(t.targets[s.target] ?? s.target, s.container_user)}</span>
                  {s.root && (
                    <>
                      {" "}
                      <Tag tone="negative">{t.root}</Tag>
                    </>
                  )}
                </TableCell>
                <TableCell className="nowrap">{dateTime(s.started_at)}</TableCell>
                <TableCell className="nowrap">
                  {s.open ? elapsed(s.started_at) : duration(s.duration_seconds)}
                </TableCell>
                <TableCell>
                  {s.open ? (
                    <Tag tone="positive">{t.openNow}</Tag>
                  ) : (
                    <span className="muted small">{t.reasons[s.reason ?? "closed"] ?? s.reason}</span>
                  )}
                </TableCell>
                <TableCell className="actions">
                  {s.open ? (
                    <Button
                      size="sm"
                      variant="danger"
                      icon={<Square />}
                      loading={end.isPending && end.variables === s.id}
                      onClick={() => end.mutate(s.id)}
                    >
                      {t.end}
                    </Button>
                  ) : s.recording ? (
                    <Button size="sm" icon={<Play />} onClick={() => setPlaying(s)}>
                      {t.play}
                    </Button>
                  ) : (
                    <span className="faint small">{s.recorded ? t.recordingRemoved : t.notRecorded}</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {playing && <ShellPlayer session={playing} onClose={() => setPlaying(null)} />}
    </div>
  );
}
