// A dialog on a phone (components/app/overlay.tsx Modal): shadcn's Drawer, from the bottom edge. In a file of its
// own, loaded the first time a dialog opens on a phone: the drawer's library (vaul) adds a <style> element as it
// loads, which the panel's Content-Security-Policy refuses (its rules are in styles/vaul.css instead).

import type { ComponentProps, ReactNode } from "react";
import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
} from "@/components/ui/drawer";

export function ModalDrawer({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  close,
  contentProps,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  close: ReactNode;
  contentProps: Partial<ComponentProps<typeof DrawerContent>>;
}) {
  return (
    <Drawer open={open} onOpenChange={onOpenChange} repositionInputs={false}>
      <DrawerContent className="modal-sheet" {...contentProps}>
        <DrawerHeader className="modal-head">
          <div className="spacer">
            <DrawerTitle>{title}</DrawerTitle>
            {description && <DrawerDescription>{description}</DrawerDescription>}
          </div>
          {close}
        </DrawerHeader>
        {children && <div className="modal-body">{children}</div>}
        {footer && <DrawerFooter className="modal-foot">{footer}</DrawerFooter>}
      </DrawerContent>
    </Drawer>
  );
}
