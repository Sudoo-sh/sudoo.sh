// What "Copy diagnostics" in the help panel copies: the panel's version, the browser, the page, the time, and the
// request id of the last error the API answered with. Never a password, a token, an instance's address or anything
// typed: only these few facts, which support needs to find the request in the server's log.

let last: { requestId: string; status: number; at: string } | null = null;

/** Remember the last failed request (called by api/client.ts for every error answer that has an id). */
export function noteError(requestId: string | undefined, status: number) {
  if (requestId) last = { requestId, status, at: new Date().toISOString() };
}

export const lastError = () => last;

/** The browser and system as the browser names them, shortened (no extensions, no fingerprinting details). */
function browser(): string {
  const agent = navigator.userAgent;
  const found = [/Edg\/[\d.]+/, /OPR\/[\d.]+/, /Firefox\/[\d.]+/, /Chrome\/[\d.]+/, /Version\/[\d.]+.*Safari/]
    .map((pattern) => agent.match(pattern)?.[0])
    .find(Boolean);
  const system = agent.match(/\(([^)]+)\)/)?.[1]?.split(";")[0] ?? "";
  return (
    [found?.replace(/^Version\/([\d.]+).*Safari/, "Safari/$1"), system].filter(Boolean).join(", ") || agent.slice(0, 80)
  );
}

export function diagnostics(version: string, pathname: string, language: string): string {
  const error = lastError();
  return [
    `Sudoo.sh panel ${version || "?"}`,
    `Page: ${pathname}`,
    `Time: ${new Date().toISOString()}`,
    `Browser: ${browser()}`,
    `Language: ${language}; screen ${window.innerWidth}x${window.innerHeight}`,
    error ? `Last error: request ${error.requestId} (HTTP ${error.status}) at ${error.at}` : "Last error: none",
  ].join("\n");
}
