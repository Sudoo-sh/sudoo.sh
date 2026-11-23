import type { ReactNode } from "react";
import { Sparkline } from "./charts/Sparkline";
import { Skeleton } from "./app/basics";
import { AnimatedNumber } from "./app/motion";

/** A figure with its label, a line of how it moved over the last hour, and what it compares to. */
export function StatCard({
  label,
  value,
  unit,
  detail,
  decimals = 0,
  trend,
}: {
  label: string;
  /** undefined while loading, null when there is nothing to show */
  value: number | null | undefined;
  unit?: string;
  detail: ReactNode;
  decimals?: number;
  trend?: { t: number[]; values: (number | null)[]; max?: number; color?: string };
}) {
  return (
    <div className="card stat">
      <span className="label">{label}</span>
      <span className="figure">
        {value === undefined ? (
          <Skeleton height={32} width={72} />
        ) : value === null ? (
          "—"
        ) : (
          // counts up from 0 when the figure first arrives, then glides to each new value
          <AnimatedNumber value={value} from={0} format={(n) => n.toFixed(decimals)} />
        )}
        {value != null && unit && <small>{unit}</small>}
      </span>
      {trend && <Sparkline t={trend.t} values={trend.values} max={trend.max} color={trend.color} />}
      <span className="detail">{detail}</span>
    </div>
  );
}
