// The panel's own question dialog, in place of the browser's confirm(): `const [confirm, dialog] = useConfirm()`, show
// {dialog} once in the page, then `if (await confirm("Delete it?")) ...`. It is the Modal dialog (a drawer on a
// phone), so it takes focus, closes with Escape, reads aloud with its title and description, and follows the theme and
// the language. Escape, the close button, a click outside and Cancel all answer "no". The browser's confirm(), alert()
// and prompt() are not used anywhere in src (see no-native-dialogs.test.ts).

import { useCallback, useRef, useState, type ReactNode } from "react";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import { Button } from "./basics";
import { Modal } from "./overlay";

export interface Question {
  /** what is asked: the dialog's description */
  text: ReactNode;
  /** the dialog's title; defaults to a short "Are you sure?" */
  title?: ReactNode;
  /** the label of the button that says yes; defaults to "Confirm" */
  action?: ReactNode;
  /** a destructive answer: the yes button is red */
  danger?: boolean;
}

export function ConfirmDialog({ question, onAnswer }: { question: Question; onAnswer: (yes: boolean) => void }) {
  const all = useT(common).common;
  return (
    <Modal
      title={question.title ?? all.areYouSure}
      description={question.text}
      onClose={() => onAnswer(false)}
      footer={
        <>
          <Button onClick={() => onAnswer(false)}>{all.cancel}</Button>
          <Button variant={question.danger ? "danger" : "primary"} onClick={() => onAnswer(true)}>
            {question.action ?? all.confirm}
          </Button>
        </>
      }
    />
  );
}

/** Ask a yes or no question. Returns the function to ask with and the dialog to draw once in the page. */
export function useConfirm() {
  const [question, setQuestion] = useState<Question | null>(null);
  const waiting = useRef<((yes: boolean) => void) | null>(null);
  // what had the focus when the question was asked (for a menu item: the button that opened the menu)
  const opener = useRef<HTMLElement | null>(null);
  const answer = useCallback((yes: boolean) => {
    const resolve = waiting.current;
    waiting.current = null;
    setQuestion(null);
    resolve?.(yes);
    // the dialog gives the focus back once it has closed, to where the question came from
    const back = opener.current;
    opener.current = null;
    window.setTimeout(() => {
      if (back?.isConnected && !document.querySelector('[role="dialog"]')) back.focus({ preventScroll: true });
    }, 300);
  }, []);
  const confirm = useCallback(
    (text: ReactNode | Question, options?: Omit<Question, "text">): Promise<boolean> =>
      new Promise((resolve) => {
        // a question still open is answered "no" before the next one is asked
        waiting.current?.(false);
        waiting.current = resolve;
        const active = document.activeElement as HTMLElement | null;
        const menu = active?.closest('[role="menu"]') ?? document.querySelector('[role="menu"]');
        const trigger = menu ? document.getElementById(menu.getAttribute("aria-labelledby") ?? "") : null;
        opener.current = trigger?.querySelector<HTMLElement>("button, a") ?? trigger ?? active;
        const given = typeof text === "object" && text !== null && "text" in text;
        setQuestion(given ? (text as Question) : { text: text as ReactNode, ...options });
      }),
    [],
  );
  const dialog = question ? <ConfirmDialog question={question} onAnswer={answer} /> : null;
  return [confirm, dialog] as const;
}
