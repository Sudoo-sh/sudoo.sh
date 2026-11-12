import { Link } from "react-router";

/** The Sudoo.sh mark: Odoo's round "o" followed by a text cursor, on the accent. The same drawing is the favicon
 * (public/favicon.svg), the logo in docs/images/brand/, the status pages and the e-mails. */
export function Mark({ size = 26 }: { size?: number }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true">
      <rect width="64" height="64" rx="15" fill="var(--accent)" />
      <g transform="translate(32 32) scale(1.9467) translate(-12 -12)" fill="none">
        <circle cx="10" cy="12" r="4.6" stroke="var(--on-accent)" strokeWidth="2.4" />
        <rect x="16.1" y="6.4" width="2.6" height="11.2" rx="1.3" fill="var(--on-accent)" />
      </g>
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
