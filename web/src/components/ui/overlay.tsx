import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Button } from "./basics";

/** Close after the exit transition: the element gets data-closing, then onClose runs once it has left. */
function useClosing(onClose: () => void, ms: number): [boolean, () => void] {
  const [closing, setClosing] = useState(false);
  const close = useCallback(() => {
    setClosing(true);
    setTimeout(onClose, matchMedia("(prefers-reduced-motion: reduce)").matches ? 150 : ms);
  }, [onClose, ms]);
  return [closing, close];
}

function useEscape(onClose: () => void) {
  useEffect(() => {
    const listener = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [onClose]);
}

function useFocusFirst<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => {
    const first = ref.current?.querySelector<HTMLElement>("[autofocus], input, select, textarea, button.primary");
    first?.focus();
  }, []);
  return ref;
}

export function Modal({
  title,
  description,
  onClose,
  children,
  footer,
  wide,
}: {
  title: ReactNode;
  description?: ReactNode;
  onClose: () => void;
  children?: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const [closing, close] = useClosing(onClose, 200);
  useEscape(close);
  const ref = useFocusFirst<HTMLDivElement>();
  return createPortal(
    <div className="overlay" data-closing={closing || undefined} onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" ref={ref}>
        <div className="modal-head">
          <div className="spacer">
            <h2>{title}</h2>
            {description && <p>{description}</p>}
          </div>
          <Button variant="ghost" size="sm" icon={<X />} onClick={close} aria-label="Close" />
        </div>
        {children && <div className="modal-body">{children}</div>}
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer({ title, onClose, children, actions }: { title: ReactNode; onClose: () => void; children: ReactNode; actions?: ReactNode }) {
  const [closing, close] = useClosing(onClose, 280);
  useEscape(close);
  return createPortal(
    <div className="overlay drawer-overlay" data-closing={closing || undefined} onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="drawer-head">
          <div className="spacer">{title}</div>
          {actions}
          <Button variant="ghost" size="sm" icon={<X />} onClick={close} aria-label="Close" />
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>,
    document.body,
  );
}

/** A button that opens a small menu next to it; the menu grows from the button and shrinks back into it.
 * It is drawn above the page (a portal with fixed position), so a scrolling list or a card never cuts it off, and
 * it opens upwards when there is no room below. */
export function Menu({ trigger, children, align = "right" }: { trigger: (open: () => void) => ReactNode; children: (close: () => void) => ReactNode; align?: "left" | "right" }) {
  const [open, setOpenState] = useState(false);
  const [closing, setClosing] = useState(false);
  const [place, setPlace] = useState<CSSProperties>({});
  const anchor = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const setOpen = useCallback((value: boolean | ((current: boolean) => boolean)) => {
    setOpenState((current) => {
      const next = typeof value === "function" ? value(current) : value;
      if (current && !next) {
        setClosing(true);
        setTimeout(() => setClosing(false), 120);
      }
      return next;
    });
  }, []);

  // where the menu goes: next to the button, below it when it fits, above it otherwise
  useLayoutEffect(() => {
    if (!open || !anchor.current) return;
    const button = anchor.current.getBoundingClientRect();
    const height = menu.current?.offsetHeight ?? 0;
    const gap = 6;
    const below = align === "right" && button.bottom + gap + height <= window.innerHeight - 8;
    const horizontal = align === "right" ? { right: Math.max(8, window.innerWidth - button.right) } : { left: Math.max(8, button.left) };
    const vertical = below ? { top: button.bottom + gap } : { bottom: window.innerHeight - button.top + gap };
    const origin = `${below ? "top" : "bottom"} ${align === "right" ? "right" : "left"}`;
    setPlace({ position: "fixed", ...horizontal, ...vertical, ["--origin" as string]: origin });
  }, [open, align]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!anchor.current?.contains(target) && !menu.current?.contains(target)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => event.key === "Escape" && setOpen(false);
    const away = () => setOpen(false); // the button moved: close rather than float in the wrong place
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    window.addEventListener("resize", away);
    window.addEventListener("scroll", away, true);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
      window.removeEventListener("resize", away);
      window.removeEventListener("scroll", away, true);
    };
  }, [open, setOpen]);

  return (
    <div ref={anchor} style={{ display: "inline-flex" }}>
      {trigger(() => setOpen((value) => !value))}
      {(open || closing) &&
        createPortal(
          <div ref={menu} className="menu" role="menu" data-closing={(!open && closing) || undefined} style={place}>
            {children(() => setOpen(false))}
          </div>,
          document.body,
        )}
    </div>
  );
}

// ---------------------------------------------------------------- toasts

type Tone = "success" | "error" | "info";
interface Toast {
  id: number;
  tone: Tone;
  title: string;
  text?: string;
  closing?: boolean;
}
const ToastContext = createContext<(tone: Tone, title: string, text?: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((tone: Tone, title: string, text?: string) => {
    const id = Date.now() + Math.random();
    setToasts((list) => [...list.slice(-3), { id, tone, title, text }]);
    const shown = tone === "error" ? 7000 : 4000;
    setTimeout(() => setToasts((list) => list.map((t) => (t.id === id ? { ...t, closing: true } : t))), shown);
    setTimeout(() => setToasts((list) => list.filter((t) => t.id !== id)), shown + 320);
  }, []);
  const icon = { success: <CircleCheck />, error: <CircleAlert />, info: <Info /> };
  return (
    <ToastContext.Provider value={push}>
      {children}
      {createPortal(
        <div className="toasts" aria-live="polite">
          {toasts.map((toast) => (
            <div key={toast.id} className={`toast ${toast.tone}`} data-closing={toast.closing || undefined}>
              {icon[toast.tone]}
              <div>
                <div className="title">{toast.title}</div>
                {toast.text && <div className="text">{toast.text}</div>}
              </div>
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
