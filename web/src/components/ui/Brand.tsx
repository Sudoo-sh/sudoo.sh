import { Link } from "react-router";

/** The Sudoo.sh mark: a ring with a dot and a text cursor on a rounded tile. The same drawing is the favicon
 * (public/favicon.svg), docs/images/brand/, the status pages and the e-mails. Here it follows the theme: the tile is
 * the surface colour and the glyph the accent (BRAND_COLOR), so light shows plum on white, dark light plum on graphite. */
export function Mark({ size = 26 }: { size?: number }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true">
      <rect x="0.5" y="0.5" width="63" height="63" rx="14" fill="var(--surface)" stroke="var(--hairline-strong)" />
      <circle cx="27.4" cy="35.6" r="10.6" fill="none" stroke="var(--accent)" strokeWidth="5.3" />
      <circle cx="38.8" cy="21.3" r="2.65" fill="var(--accent)" />
      <rect x="45.1" y="18.8" width="3.3" height="29.7" rx="0.4" fill="var(--accent)" />
    </svg>
  );
}

export function Brand({ to = "/" }: { to?: string }) {
  return (
    <Link to={to} className="brand" viewTransition>
      <span className="mark">
        <Mark />
      </span>
      <span>
        Sudoo<span className="tld">.sh</span>
      </span>
    </Link>
  );
}
