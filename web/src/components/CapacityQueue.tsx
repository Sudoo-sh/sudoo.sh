import { Hourglass } from "lucide-react";
import { Link } from "react-router";
import { call, errorText } from "@/api/client";
import { useCall, useCapacityQueue } from "@/api/queries";
import type { WaitingRequest } from "@/api/types";
import { useT } from "@/i18n";
import words from "@/i18n/capacity-queue";
import { Button, Tag } from "./app/basics";
import { useToast } from "./app/overlay";
import { useCan } from "./Session";
import { StageTag } from "./StageTag";

function Row({ item, manager }: { item: WaitingRequest; manager: boolean }) {
  const t = useT(words);
  const toast = useToast();
  const can = useCan();
  const refresh = [["capacity-queue"], ["instances"]] as const;
  const cancel = useCall(() => call("DELETE", `/capacity-queue/${item.id}`), refresh);
  const front = useCall(() => call("POST", `/capacity-queue/${item.id}/front`, {}), refresh);
  const run = (action: typeof cancel, done: string) =>
    action.mutate(undefined, {
      onSuccess: () => toast("success", done),
      onError: (error) => toast("error", t.failed, errorText(error)),
    });
  return (
    <li className="expiry-row">
      <div className="expiry-name">
        {item.kind === "start" ? (
          <Link to={`/i/${item.instance}`} viewTransition className="truncate">
            {item.instance}
          </Link>
        ) : (
          <span className="truncate font-semibold">{item.instance}</span>
        )}
        <span className="sub truncate">
          {item.kind === "create" ? t.create : t.start}
          {manager ? ` · ${t.by(item.user)}` : ""}
        </span>
      </div>
      {item.stage ? <StageTag stage={item.stage as never} /> : <span />}
      <span className="expiry-when">
        <Tag tone="accent">{t.position(item.position)}</Tag>
      </span>
      <span className="flex gap-2">
        {can("capacity.manage") && (item.position ?? 1) > 1 && (
          <Button
            size="sm"
            variant="soft"
            aria-label={t.frontLabel(item.instance)}
            loading={front.isPending}
            onClick={() => run(front, t.moved(item.instance))}
          >
            {t.front}
          </Button>
        )}
        <Button
          size="sm"
          variant="soft"
          aria-label={t.cancelLabel(item.instance)}
          loading={cancel.isPending}
          onClick={() => run(cancel, t.cancelled(item.instance))}
        >
          {t.cancel}
        </Button>
      </span>
    </li>
  );
}

/** The home page's card of requests waiting for room under a running-instances limit: your own, or everyone's with
 *  capacity.manage. Nothing is drawn when nothing waits (the queue is off by default). */
export function CapacityQueue() {
  const t = useT(words);
  const can = useCan();
  const { data } = useCapacityQueue();
  if (!data?.length) return null;
  const manager = can("capacity.manage");
  return (
    <section className="card expiry-card rise" aria-labelledby="capacity-queue-title">
      <div className="expiry-head">
        <Hourglass aria-hidden />
        <div>
          <h2 id="capacity-queue-title">{t.title}</h2>
          <p className="sub">{manager ? t.textAll : t.text}</p>
        </div>
      </div>
      <ul className="expiry-list">
        {data.map((item) => (
          <Row key={item.id} item={item} manager={manager} />
        ))}
      </ul>
    </section>
  );
}
