import { Check, CircleAlert, Copy as CopyIcon, Info, TriangleAlert } from "lucide-react";
import {
  cloneElement,
  isValidElement,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button as UIButton } from "@/components/ui/button";
import { FieldDescription, FieldError, FieldLabel, Field as UIField } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Progress as UIProgress } from "@/components/ui/progress";
import { Skeleton as UISkeleton } from "@/components/ui/skeleton";
import { Switch as UISwitch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";
import { SelectBox } from "@/components/app/select";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import { statusLabel } from "@/lib/format";

type Variant = "primary" | "danger" | "ghost" | "soft" | "default";

// the panel's button names on shadcn's: the plain button is "outline", the plum one "default"
const BUTTON_VARIANT = { default: "outline", primary: "default", ghost: "ghost", soft: "secondary", danger: "destructive" } as const;

/** A button (shadcn's Button). `asChild` draws its one child (a link) as the button; `icon` comes before the words,
 *  and a button with an icon and no words is square. */
export function Button({
  variant = "default",
  size,
  icon,
  loading,
  block,
  asChild,
  children,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "sm" | "lg";
  icon?: ReactNode;
  loading?: boolean;
  block?: boolean;
  asChild?: boolean;
}) {
  const square = !children;
  const shape = square ? (size ? (`icon-${size}` as const) : "icon") : (size ?? "default");
  const classes = cn(block && "w-full", className);
  if (asChild)
    return (
      <UIButton asChild variant={BUTTON_VARIANT[variant]} size={shape} className={classes} {...props}>
        {/* the icon goes inside the child (a link), before its words */}
        <SlotWithIcon icon={icon}>{children}</SlotWithIcon>
      </UIButton>
    );
  return (
    <UIButton type="button" variant={BUTTON_VARIANT[variant]} size={shape} className={classes} disabled={loading || props.disabled} {...props}>
      {loading ? <span className="spinner" /> : icon}
      {children}
    </UIButton>
  );
}

/** Renders its one child element with the button's props, the icon put before the child's own content. */
function SlotWithIcon({ icon, children, ...props }: { icon?: ReactNode; children?: ReactNode }) {
  if (!isValidElement<{ children?: ReactNode }>(children)) return null;
  return cloneElement(children, props, icon, children.props.children);
}

export const Spinner = () => <span className="spinner" role="status" aria-label={useT(common).common.loading} />;

/** A quiet label: versions, roles, counts (shadcn's Badge, outlined). */
export function Tag({ tone, mono, children, icon }: { tone?: "accent" | "positive" | "caution" | "negative"; mono?: boolean; children: ReactNode; icon?: ReactNode }) {
  return (
    <Badge variant="outline" data-tone={tone} className={mono ? "font-mono" : undefined}>
      {icon}
      {children}
    </Badge>
  );
}

/** How something is, as a dot and a word. The dot moves only while something is running. */
export function Status({ status, label }: { status: string; label?: string }) {
  return (
    <span className={`status ${status}`}>
      <span className="dot" />
      {label ?? statusLabel(status)}
    </span>
  );
}

/** An on/off switch (shadcn's Switch) with its words; clicking the words switches it too. */
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
    <label className="switch-field" data-disabled={disabled || undefined}>
      <UISwitch checked={checked} disabled={disabled} onCheckedChange={onChange} />
      {label && <span>{label}</span>}
    </label>
  );
}

// Radix reads the empty value as nothing chosen: an option whose value is "" stands for it here
const EMPTY = "__empty__";

/** A segmented control (shadcn's ToggleGroup, one choice) whose thumb slides to the chosen option, as on Apple
 *  platforms. The arrow keys move between the options. */
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
    const chosen = group.current?.querySelector<HTMLElement>('[data-slot="toggle-group-item"][data-state="on"]');
    if (chosen) setThumb({ x: chosen.offsetLeft, width: chosen.offsetWidth });
  }, [value, options.length]);
  return (
    <ToggleGroup
      type="single"
      spacing={0}
      className="segmented"
      ref={group}
      value={value === "" ? EMPTY : value}
      // a click on the chosen option would clear the choice: it stays
      onValueChange={(next) => next && onChange((next === EMPTY ? "" : next) as T)}
    >
      {thumb && <span className="thumb" style={{ width: thumb.width, transform: `translateX(${thumb.x}px)` }} />}
      {options.map((option) => (
        <ToggleGroupItem key={option.value} value={option.value === "" ? EMPTY : option.value} disabled={option.disabled}>
          {option.icon}
          {option.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

/** A form field (shadcn's Field): the label, the control, and a hint or an error under it. */
const LABELLED = new Set<unknown>([Input, Textarea, SelectBox]);

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
  // the label names its control: `htmlFor`, else the one Input, Textarea or SelectBox inside (given an id if it has none)
  const auto = useId();
  const control =
    !htmlFor && label && isValidElement<{ id?: string }>(children) && LABELLED.has(children.type) ? children : null;
  const id = htmlFor ?? (control ? (control.props.id ?? auto) : undefined);
  return (
    <UIField className={className} data-invalid={error ? true : undefined}>
      {label && <FieldLabel htmlFor={id}>{label}</FieldLabel>}
      {control && !control.props.id ? cloneElement(control, { id }) : children}
      {error ? <FieldError>{error}</FieldError> : hint && <FieldDescription>{hint}</FieldDescription>}
    </UIField>
  );
}

/** Progress of a job (shadcn's Progress): sweeping while the share is not known, red when it failed, green when done. */
export function Progress({ value, status }: { value: number | null | undefined; status?: string }) {
  const unknown = value == null && status !== "success" && status !== "failed";
  const share = status === "success" ? 100 : status === "failed" ? Math.max(value ?? 100, 8) : (value ?? 0);
  const tone = status === "failed" ? "failed" : status === "success" ? "success" : undefined;
  return <UIProgress value={unknown ? null : share} data-tone={tone} />;
}

export const Skeleton = ({ height = 16, width = "100%" }: { height?: number; width?: number | string }) => (
  <UISkeleton style={{ height, width }} />
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
/** A note in a grey box (shadcn's Alert); only the icon carries the tone. */
export function Callout({ tone = "info", children }: { tone?: keyof typeof CALLOUT_ICON; children: ReactNode }) {
  return (
    <Alert data-tone={tone}>
      {CALLOUT_ICON[tone]}
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  );
}

export function CopyField({ value, block, secret }: { value: string; block?: boolean; secret?: boolean }) {
  const t = useT(common).common;
  const [copied, setCopied] = useState(false);
  const [shown, setShown] = useState(!secret);
  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };
  return (
    <span className={`copy ${block ? "block" : ""}`}>
      <span className="value" dir="ltr">{shown ? value : "••••••••••••"}</span>
      {secret && (
        <Button size="sm" variant="ghost" onClick={() => setShown(!shown)}>
          {shown ? t.hide : t.show}
        </Button>
      )}
      <Button size="sm" variant="ghost" icon={copied ? <Check /> : <CopyIcon />} onClick={copy} aria-label={t.copy} title={t.copy} />
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
