import * as React from "react";
import { X } from "lucide-react";
import { cn } from "cn";
import { useT } from "@/i18n";
import common from "@/i18n/common";

const INPUT =
  "h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1 text-base shadow-xs transition-[color,box-shadow] outline-none file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40";

/** Empties a controlled or uncontrolled input the way typing does, so its onChange runs, then focuses it. */
function empty(input: HTMLInputElement) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set?.call(input, "");
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.focus();
}

/** The clear button at the input's inline end (the left in Arabic): its words are read only when it is shown. */
function ClearButton({ onClear }: { onClear: () => void }) {
  const label = useT(common).common.clear;
  return (
    <button
      type="button"
      data-slot="input-clear"
      className="input-clear"
      aria-label={label}
      title={label}
      onClick={onClear}
    >
      <X aria-hidden="true" />
    </button>
  );
}

/**
 * shadcn's Input. `clearable`: a small (x) button inside the field, at its inline end, that empties the field and
 * focuses it; hidden while the field is empty, disabled or read-only. `onClear` runs after it was emptied.
 */
function Input({
  className,
  type,
  clearable,
  onClear,
  ref,
  ...props
}: React.ComponentProps<"input"> & { clearable?: boolean; onClear?: () => void }) {
  const own = React.useRef<HTMLInputElement | null>(null);
  const [typed, setTyped] = React.useState(String(props.defaultValue ?? ""));
  const setRef = React.useCallback(
    (node: HTMLInputElement | null) => {
      own.current = node;
      if (typeof ref === "function") ref(node);
      else if (ref) ref.current = node;
    },
    [ref],
  );
  if (!clearable) return <input type={type} data-slot="input" className={cn(INPUT, className)} ref={ref} {...props} />;
  const value = props.value !== undefined ? String(props.value ?? "") : typed;
  const shown = value !== "" && !props.disabled && !props.readOnly;
  return (
    // styles/_look.scss: the button sits at the page's inline end, also for a left-to-right field in Arabic
    <span className="input-clearable" data-filled={shown ? "" : undefined}>
      <input
        type={type}
        data-slot="input"
        className={cn(INPUT, className)}
        ref={setRef}
        {...props}
        onChange={(event) => {
          setTyped(event.target.value);
          props.onChange?.(event);
        }}
      />
      {shown && (
        <ClearButton
          onClear={() => {
            if (own.current) empty(own.current);
            onClear?.();
          }}
        />
      )}
    </span>
  );
}

export { Input };
