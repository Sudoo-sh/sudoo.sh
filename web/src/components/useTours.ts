import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { keys } from "@/api/queries";
import type { Account, Session, ToursState } from "@/api/types";

/** Saves the person's tour state on the server ({"seen"} and/or {"tips"}) and updates what the app holds. */
export function useSaveTours() {
  const client = useQueryClient();
  return async (change: { mark?: string[]; seen?: []; tips?: boolean }): Promise<boolean> => {
    try {
      const account = await api.patch<Account>("/account", { tours: change });
      client.setQueryData(keys.account, account);
      client.setQueryData<Session | null>(
        keys.session,
        (session) => session && { ...session, tours: account.tours as ToursState },
      );
      return true;
    } catch {
      return false; // not saved: the tour may show again, nothing else is lost
    }
  };
}
