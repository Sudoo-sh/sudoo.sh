import { Check, CircleAlert, Copy as CopyIcon, Info, TriangleAlert } from "lucide-react";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import { STATUS_LABEL } from "@/lib/format";

type Variant = "primary" | "danger" | "ghost" | "soft" | "default";

export function Button({
  variant = "default",
  size,
  icon,
  loading,
  block,
  children,
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "sm" | "lg";
  icon?: ReactNode;
  loading?: boolean;
  block?: boolean;
}) {
  const classes = ["btn", variant !== "default" && variant, size, block && "block", !children && "icon", className]
    .filter(Boolean)
    .join(" ");
  return (
    <button type="button" className={classes} disabled={loading || props.disabled} {...props}>
      {loading ? <span className="spinner" /> : icon}
      {children}
    </button>
  );
}

export const Spinner = () => <span className="spinner" role="status" aria-label="Loading" />;

/** A quiet label: versions, roles, counts. */
export function Tag({ tone, mono, children, icon }: { tone?: "accent" | "positive" | "caution" | "negative"; mono?: boolean; children: ReactNode; icon?: ReactNode }) {
  return (
    <span className={["tag", tone, mono && "mono"].filter(Boolean).join(" ")}>
      {icon}
      {children}
    </span>
  );
}

/** How something is, as a dot and a word. The dot moves only while something is running. */
export function Status({ status, label }: { status: string; label?: string }) {
  return (
    <span className={`status ${status}`}>
      <span className="dot" />
      {label ?? STATUS_LABEL[status] ?? status}
    </span>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className="switch">
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="track" />
      {label && <span>{label}</span>}
    </label>
  );
}

/** A segmented control whose thumb slides to the chosen option, as on Apple platforms. */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: ReactNode; disabled?: boolean; icon?: ReactNode }[];
  onChange: (value: T) => void;
}) {
  const group = useRef<HTMLDivElement>(null);
  const [thumb, setThumb] = useState<{ x: number; width: number } | null>(null);
  useLayoutEffect(() => {
    const chosen = group.current?.querySelector<HTMLElement>('button[aria-pressed="true"]');
    if (chosen) setThumb({ x: chosen.offsetLeft, width: chosen.offsetWidth });
  }, [value, options.length]);
  return (
    <div className="segmented" role="group" ref={group}>
      {thumb && <span className="thumb" style={{ width: thumb.width, transform: `translateX(${thumb.x}px)` }} />}
      {options.map((option) => (
        <button
          type="button"
          key={option.value}
          aria-pressed={option.value === value}
          disabled={option.disabled}
          onClick={() => onChange(option.value)}
        >
          {option.icon}
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Field({
  label,
  hint,
  error,
  children,
  className,
  htmlFor,
}: {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
  className?: string;
  htmlFor?: string;
}) {
  return (
    <div className={`field ${className ?? ""}`}>
      {label && <label htmlFor={htmlFor}>{label}</label>}
      {children}
      {error ? <span className="error">{error}</span> : hint && <span className="hint">{hint}</span>}
    </div>
  );
}

export function Progress({ value, status }: { value: number | null | undefined; status?: string }) {
  const unknown = value == null && status !== "success" && status !== "failed";
  const share = status === "success" ? 100 : status === "failed" ? Math.max(value ?? 100, 8) : (value ?? 0);
  const cls = ["progress", unknown && "indeterminate", status === "failed" && "failed", status === "success" && "success"]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={cls} role="progressbar" aria-valuenow={value ?? undefined} aria-valuemin={0} aria-valuemax={100}>
      <div className="bar" style={unknown ? undefined : { transform: `scaleX(${share / 100})` }} />
    </div>
  );
}

export const Skeleton = ({ height = 16, width = "100%" }: { height?: number; width?: number | string }) => (
  <div className="skeleton" style={{ height, width }} />
);

export function Empty({ icon, title, children, action }: { icon: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      {icon}
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

const CALLOUT_ICON = { info: <Info />, warning: <TriangleAlert />, danger: <CircleAlert />, success: <Check /> };
export function Callout({ tone = "info", children }: { tone?: keyof typeof CALLOUT_ICON; children: ReactNode }) {
  return (
    <div className={`callout ${tone}`}>
      {CALLOUT_ICON[tone]}
      <div>{children}</div>
    </div>
  );
}

export function CopyField({ value, block, secret }: { value: string; block?: boolean; secret?: boolean }) {
  const [copied, setCopied] = useState(false);
  const [shown, setShown] = useState(!secret);
  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };
  return (
    <span className={`copy ${block ? "block" : ""}`}>
      <span className="value">{shown ? value : "••••••••••••"}</span>
      {secret && (
        <Button size="sm" variant="ghost" onClick={() => setShown(!shown)}>
          {shown ? "Hide" : "Show"}
        </Button>
      )}
      <Button size="sm" variant="ghost" icon={copied ? <Check /> : <CopyIcon />} onClick={copy} aria-label="Copy" title="Copy" />
    </span>
  );
}

/** A number that glides to its new value instead of jumping. */
export function AnimatedNumber({ value, format = (n) => n.toFixed(0) }: { value: number; format?: (n: number) => string }) {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    const start = performance.now();
    const begin = from.current;
    let frame = 0;
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / 600);
      const eased = 1 - Math.pow(1 - t, 3);
      const current = begin + (value - begin) * eased;
      setShown(current);
      from.current = current;
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [value]);
  return <>{format(shown)}</>;
}
