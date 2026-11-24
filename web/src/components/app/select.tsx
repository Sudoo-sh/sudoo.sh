import {
  Children,
  Fragment,
  isValidElement,
  type CSSProperties,
  type OptionHTMLAttributes,
  type ReactNode,
} from "react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

// Radix gives the empty value a meaning of its own (nothing chosen): an option whose value is "" stands for it here
const EMPTY = "__empty__";
const encode = (value: string) => (value === "" ? EMPTY : value);
const decode = (value: string) => (value === EMPTY ? "" : value);

interface Option {
  key: string;
  value: string;
  label: ReactNode;
  disabled?: boolean;
  lang?: string;
}

/** The <option> elements given as children (in arrays and fragments too), as a native <select> reads them: an
 *  option without a value has its text as its value. */
function optionsOf(children: ReactNode): Option[] {
  const options: Option[] = [];
  Children.forEach(children, (child) => {
    if (!isValidElement<OptionHTMLAttributes<HTMLOptionElement> & { children?: ReactNode }>(child)) return;
    if (child.type === Fragment) options.push(...optionsOf(child.props.children));
    if (child.type !== "option") return;
    const { value, children: label, disabled, lang } = child.props;
    const text = value != null ? String(value) : Children.toArray(label).join("");
    options.push({ key: child.key ?? text, value: text, label, disabled, lang });
  });
  return options;
}

/** A list to choose one value from: shadcn's Select (a designed list, with a check mark on the chosen value), written
 *  like a native <select> with <option> children. `onValueChange` gets the chosen option's value. */
export function SelectBox({
  value,
  onValueChange,
  children,
  className,
  size,
  disabled,
  placeholder,
  dir,
  id,
  name,
  style,
  "aria-label": label,
}: {
  value: string | number | null | undefined;
  onValueChange: (value: string) => void;
  children: ReactNode;
  className?: string;
  size?: "sm" | "default";
  disabled?: boolean;
  placeholder?: ReactNode;
  /** "ltr" for values read left to right in every language (branches, versions, accounts) */
  dir?: "ltr";
  id?: string;
  name?: string;
  style?: CSSProperties;
  "aria-label"?: string;
}) {
  const options = optionsOf(children);
  const current = String(value ?? "");
  // like a native <select>, a value that is none of the options shows the first one
  const shown = options.some((o) => o.value === current) ? current : (options[0]?.value ?? current);
  return (
    <Select value={encode(shown)} onValueChange={(next) => onValueChange(decode(next))} disabled={disabled} name={name}>
      <SelectTrigger id={id} size={size} className={className} style={style} aria-label={label}>
        <span className="select-value">
          {/* every option, unseen: the field is as wide as the longest one, like a native <select> */}
          {options.map((o) => (
            <span key={o.key} className="select-sizer" aria-hidden="true">
              {o.label}
            </span>
          ))}
          <SelectValue placeholder={placeholder} />
        </span>
      </SelectTrigger>
      <SelectContent position="popper" sideOffset={4} collisionPadding={8}>
        {options.map((o) => (
          <SelectItem key={o.key} value={encode(o.value)} disabled={o.disabled} lang={o.lang}>
            {dir ? <span dir={dir}>{o.label}</span> : o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
