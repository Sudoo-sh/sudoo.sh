/** The tabs of the Accounts page: who is in each. Pending approval and Invited come from their own lists. */
export type AccountTab = "pending" | "active" | "invited" | "disabled";

export const ACCOUNT_TABS: AccountTab[] = ["pending", "active", "invited", "disabled"];

/** The tab of an account: Disabled when it is, Active otherwise. */
export const accountTab = (user: { disabled?: boolean }): "active" | "disabled" =>
  user.disabled ? "disabled" : "active";

export function tabCounts(
  users: { disabled?: boolean }[],
  invitations: unknown[],
  requests: { status: string }[],
): Record<AccountTab, number> {
  return {
    pending: requests.filter((r) => r.status === "pending").length,
    active: users.filter((u) => accountTab(u) === "active").length,
    invited: invitations.length,
    disabled: users.filter((u) => accountTab(u) === "disabled").length,
  };
}

/** The tab to open first: Pending approval when someone waits, else Active. */
export const firstTab = (counts: Record<AccountTab, number>): AccountTab => (counts.pending > 0 ? "pending" : "active");
