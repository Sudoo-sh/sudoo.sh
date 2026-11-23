// Which frame and which sign-in page a visit gets (components/Frame.tsx, pages/Login.tsx): the client portal for
// client accounts wherever they sign in, and on the client portal's own address (PORTAL_DOMAIN) before signing in;
// the panel's otherwise.

/** The frame of a signed-in session. */
export const frameFor = (role: string): "portal" | "panel" => (role === "client" ? "portal" : "panel");

/** The sign-in page's look: the portal's on its own address, the panel's elsewhere. */
export const signInLookFor = (portal: { portal?: boolean } | null | undefined): "portal" | "panel" =>
  portal?.portal ? "portal" : "panel";

/** Where a sign-in that belongs at the other address goes: that address's sign-in page; null when it is unknown. */
export function redirectTarget(redirect: { address: string; url: string } | null | undefined): string | null {
  if (!redirect?.url || !/^https?:\/\/[^/]+$/.test(redirect.url)) return null;
  return `${redirect.url}/login`;
}
