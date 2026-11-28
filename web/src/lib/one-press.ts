// One press, one request: a button's click handler wrapped so that a second click (a double click, or a click before
// the page drew the busy state) does not start the same work twice.
//  - a handler that returns a promise: further clicks are ignored until it settles;
//  - a button that shows a busy state (`loading`) whose handler starts the work without returning it (a mutation's
//    mutate()): further clicks are ignored for a double click's time, by then the button is drawn busy (disabled).

export const DOUBLE_CLICK_MS = 500;

export function onePress<E>(
  running: { current: boolean },
  handler: ((event: E) => unknown) | undefined,
  busyButton = false,
): ((event: E) => void) | undefined {
  if (!handler) return undefined;
  return (event: E) => {
    if (running.current) return;
    const result = handler(event);
    if (result instanceof Promise) {
      running.current = true;
      // the handler reports its own errors; this only frees the button again
      void result.finally(() => (running.current = false)).catch(() => undefined);
    } else if (busyButton) {
      running.current = true;
      setTimeout(() => (running.current = false), DOUBLE_CLICK_MS);
    }
  };
}
