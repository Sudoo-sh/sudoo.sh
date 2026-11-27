import { CircleAlert, Info, X } from "lucide-react";
import {
  createContext,
  lazy,
  Suspense,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ComponentProps,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Dialog as DialogPrimitive } from "radix-ui";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useDirection } from "@/components/ui/direction";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import { PHONE, useMedia } from "@/lib/media";
import { DURATION, glide, measure } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { Button } from "./basics";
import { SuccessCheck } from "./motion";

const ModalDrawer = lazy(() => import("./modal-drawer").then((module) => ({ default: module.ModalDrawer })));

/** Open until asked to close; then onClose runs once the exit animation has played (the caller removes it). */
function useClosing(onClose: () => void, ms: number): [boolean, (open: boolean) => void] {
  const [open, setOpen] = useState(true);
  // focus goes back to what had it (the button that opened it) when it closes
  const [before] = useState(() => document.activeElement as HTMLElement | null);
  useEffect(() => () => before?.focus?.({ preventScroll: true }), [before]);
  const done = useRef(onClose);
  done.current = onClose;
  const change = useCallback(
    (next: boolean) => {
      if (next) return;
      setOpen(false);
      setTimeout(() => done.current(), matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : ms);
    },
    [ms],
  );
  return [open, change];
}

/** Focus goes to the first field (or the main button) when a dialog opens, not to its close button. */
function focusFirst(event: Event) {
  const content = event.currentTarget as HTMLElement;
  const first = content.querySelector<HTMLElement>(
    '[autofocus], input:not([type="hidden"]):not([aria-hidden="true"]), textarea, [data-slot="select-trigger"], [data-slot="button"][data-variant="default"]',
  );
  event.preventDefault();
  (first ?? content).focus({ preventScroll: true });
}

/** Without a description, the dialog says so (Radix otherwise warns that it has none). */
const quiet = (description: ReactNode) => (description ? {} : { "aria-describedby": undefined });

/** A dialog: shadcn's Dialog on a wider screen, its Drawer (from the bottom edge) on a phone. It is open while it is
 *  drawn; Escape, the close button or a click outside ask it to close, and onClose runs after it has gone. */
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
  const phone = useMedia(PHONE);
  const [open, setOpen] = useClosing(onClose, phone ? 500 : 200);
  const closeLabel = useT(common).common.close;
  // a plain button: DialogClose (asChild) would put its own data-slot on it, in place of the button's
  const close = (
    <Button variant="ghost" size="sm" icon={<X />} aria-label={closeLabel} onClick={() => setOpen(false)} />
  );
  if (phone)
    return (
      <Suspense fallback={null}>
        <ModalDrawer
          open={open}
          onOpenChange={setOpen}
          title={title}
          description={description}
          footer={footer}
          close={close}
          contentProps={{ onOpenAutoFocus: focusFirst, ...quiet(description) }}
        >
          {children}
        </ModalDrawer>
      </Suspense>
    );
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent
        showCloseButton={false}
        data-wide={wide || undefined}
        onOpenAutoFocus={focusFirst}
        {...quiet(description)}
      >
        <DialogHeader className="modal-head">
          <div className="spacer">
            <DialogTitle>{title}</DialogTitle>
            {description && <DialogDescription>{description}</DialogDescription>}
          </div>
          {close}
        </DialogHeader>
        {children && <div className="modal-body">{children}</div>}
        {footer && <DialogFooter className="modal-foot">{footer}</DialogFooter>}
      </DialogContent>
    </Dialog>
  );
}

/** A panel that slides in from the inline-end edge (the right; the left in Arabic): shadcn's Sheet. */
export function Drawer({
  title,
  onClose,
  children,
  actions,
  className,
  label,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  actions?: ReactNode;
  className?: string;
  /** the dialog's name for screen readers, when the title is not plain text */
  label?: string;
}) {
  const [open, setOpen] = useClosing(onClose, 280);
  const closeLabel = useT(common).common.close;
  const end = useDirection() === "rtl" ? "left" : "right";
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetContent
        side={end}
        showCloseButton={false}
        className={cn("drawer", className)}
        aria-label={label}
        aria-describedby={undefined}
        onOpenAutoFocus={(event) => {
          // focus goes to the panel itself, not its first button
          event.preventDefault();
          (event.currentTarget as HTMLElement).focus({ preventScroll: true });
        }}
      >
        <div className="drawer-head">
          {/* the title is the visible heading; label (when given) is what a screen reader announces */}
          <DialogPrimitive.Title asChild>
            <div className="spacer">{title}</div>
          </DialogPrimitive.Title>
          {actions}
          <Button variant="ghost" size="sm" icon={<X />} aria-label={closeLabel} onClick={() => setOpen(false)} />
        </div>
        <div className="drawer-body">{children}</div>
      </SheetContent>
    </Sheet>
  );
}

/** A button that opens a small menu next to it (shadcn's DropdownMenu): it grows from the button, flips above it when
 *  there is no room below, and the arrow keys move through its items. `trigger` draws the button (call `open` from
 *  its onClick); `children` the items (MenuItem, MenuSeparator, MenuLabel), told how to close the menu. */
export function Menu({
  trigger,
  children,
  align = "right",
}: {
  trigger: (open: () => void) => ReactNode;
  children: (close: () => void) => ReactNode;
  align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const anchor = useRef<HTMLSpanElement>(null);
  // The menu's state belongs on the button `trigger` draws, which is what assistive technology reaches: the anchor
  // around it is only where the menu is placed, and a <span> may not carry aria-expanded (Radix would put it there).
  useEffect(() => {
    const button = anchor.current?.querySelector<HTMLElement>("button, a");
    if (!button) return;
    button.setAttribute("aria-haspopup", "menu");
    button.setAttribute("aria-expanded", String(open));
  }, [open]);
  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      {/* the menu opens on a press or a key on what `trigger` draws (its `open` has nothing left to do) */}
      <DropdownMenuTrigger
        asChild
        type={undefined}
        aria-haspopup={undefined}
        aria-expanded={undefined}
        aria-controls={undefined}
      >
        <span className="menu-anchor" ref={anchor}>
          {trigger(noop)}
        </span>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        // "left" and "right" are for left-to-right pages; in Arabic the menu lines up with the mirrored side
        align={align === "right" ? "end" : "start"}
        sideOffset={6}
        collisionPadding={8}
        className="menu"
        onKeyDown={legacyArrows}
        onCloseAutoFocus={(event) => {
          // back to the button that opened it
          event.preventDefault();
          const button = anchor.current?.querySelector<HTMLElement>("button, a");
          // unless a menu item opened a dialog (one this menu is not inside): the dialog has the focus then, and keeps it
          const dialogs = [...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')];
          if (dialogs.some((dialog) => !dialog.contains(anchor.current))) return;
          button?.focus({ preventScroll: true });
        }}
      >
        {children(close)}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
const noop = () => {};

/** The arrow keys in a menu of plain buttons and links (premium packages built before MenuItem): Radix moves only
 *  between its own items, so here they move between those. */
function legacyArrows(event: KeyboardEvent<HTMLDivElement>) {
  const content = event.currentTarget;
  if (content.querySelector('[role="menuitem"]')) return;
  const items = [...content.querySelectorAll<HTMLElement>("button:not(:disabled), a[href]")];
  if (!items.length) return;
  const at = items.indexOf(document.activeElement as HTMLElement);
  const next = { ArrowDown: at + 1, ArrowUp: at < 0 ? items.length - 1 : at - 1, Home: 0, End: items.length - 1 }[
    event.key
  ];
  if (next == null) return;
  event.preventDefault();
  items[(next + items.length) % items.length]?.focus();
}

/** An item of a Menu: a button, or a link with `asChild`. `danger` draws it red. */
export function MenuItem({ danger, ...props }: ComponentProps<typeof DropdownMenuItem> & { danger?: boolean }) {
  return <DropdownMenuItem variant={danger ? "destructive" : "default"} {...props} />;
}
export { DropdownMenuLabel as MenuLabel, DropdownMenuSeparator as MenuSeparator };

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
  const stack = useRef<HTMLDivElement>(null);
  // FLIP: where the toasts are just before a change, so the ones that stay glide to their new place (a new toast
  // pushes the others up smoothly instead of making them jump)
  const before = useRef<Map<string, DOMRect>>(new Map());
  const change = useCallback((update: (list: Toast[]) => Toast[]) => {
    before.current = stack.current ? measure(toastNodes(stack.current), toastKey) : new Map();
    setToasts(update);
  }, []);
  useLayoutEffect(() => {
    if (stack.current && before.current.size) glide(toastNodes(stack.current), before.current, toastKey);
    before.current = new Map();
  }, [toasts]);
  const push = useCallback(
    (tone: Tone, title: string, text?: string) => {
      const id = Date.now() + Math.random();
      change((list) => [...list.slice(-3), { id, tone, title, text }]);
      const shown = tone === "error" ? 7000 : 4000;
      // it leaves the way it came (quicker than it arrived), then the space it took closes
      setTimeout(() => setToasts((list) => list.map((t) => (t.id === id ? { ...t, closing: true } : t))), shown);
      setTimeout(() => change((list) => list.filter((t) => t.id !== id)), shown + DURATION.ui);
    },
    [change],
  );
  const icon = { success: <SuccessCheck />, error: <CircleAlert />, info: <Info /> };
  return (
    <ToastContext.Provider value={push}>
      {children}
      {createPortal(
        <div className="toasts" aria-live="polite" ref={stack}>
          {toasts.map((toast) => (
            <div
              key={toast.id}
              data-id={toast.id}
              className={`toast ${toast.tone}`}
              data-closing={toast.closing || undefined}
            >
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
const toastNodes = (stack: HTMLElement) => [...stack.children] as HTMLElement[];
const toastKey = (element: HTMLElement) => element.dataset.id;

export const useToast = () => useContext(ToastContext);
