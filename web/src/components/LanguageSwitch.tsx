// The language switch, as on the other Sudoo.sh sites: a quiet button written in the other language ("العربية" on an
// English page, "English" on an Arabic one). Signed in, the choice is saved on the account (PATCH /account
// `language`), so it follows the person to any browser; before signing in it is remembered in this browser only.

import { useQueryClient } from "@tanstack/react-query";
import { Languages } from "lucide-react";
import { api, errorText } from "@/api/client";
import { keys } from "@/api/queries";
import type { Account, Session } from "@/api/types";
import { LOCALE_CODES, LOCALES, setLocale, useLocale, useT, type Locale } from "@/i18n";
import common from "@/i18n/common";
import { Button } from "./app/basics";
import { useToast } from "./app/overlay";

/** Saves the signed-in person's language (null: the server's default) and shows the panel in it. */
export function useSaveLanguage() {
  const client = useQueryClient();
  const toast = useToast();
  const t = useT(common).language;
  return async (choice: Locale | null) => {
    try {
      const account = await api.patch<Account>("/account", { language: choice });
      client.setQueryData(keys.account, account);
      // the session's language changes the page's (main.tsx follows it)
      client.setQueryData<Session | null>(
        keys.session,
        (session) => session && { ...session, language: account.language.value },
      );
    } catch (error) {
      toast("error", t.notSaved, errorText(error));
    }
  };
}

/** The other language, written in that language. `signedIn`: saved on the account, otherwise in this browser. */
export function LanguageSwitch({ signedIn = false, className }: { signedIn?: boolean; className?: string }) {
  const current = useLocale();
  const save = useSaveLanguage();
  const other = LOCALE_CODES.find((code) => code !== current)!;
  return (
    <Button
      variant="ghost"
      size="sm"
      className={`language-switch ${className ?? ""}`.trim()}
      icon={<Languages />}
      lang={other}
      dir={LOCALES[other].dir}
      title={`${common[other].language.choose} ${LOCALES[other].name}`}
      onClick={() => (signedIn ? save(other) : setLocale(other))}
    >
      {LOCALES[other].name}
    </Button>
  );
}
