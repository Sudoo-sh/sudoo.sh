import { Link } from "react-router";

/** The Sudoo.sh mark: a terminal prompt whose cursor is Odoo's round "o". */
export function Mark({ size = 20 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" aria-hidden="true">
      <path d="M5.5 7.5 10 12l-4.5 4.5" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="16.5" cy="15.5" r="2.6" stroke="currentColor" strokeWidth="2.2" />
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
