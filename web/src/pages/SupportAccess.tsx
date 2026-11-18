import { Page, PageHead } from "@/components/Shell";
import { SupportAccessSettings } from "@/components/SupportAccess";
import { useT } from "@/i18n";
import words from "@/i18n/support";

/** Support access for administrators (super admins also find it under Settings). */
export function SupportAccessPage() {
  const t = useT(words);
  return (
    <Page>
      <PageHead title={t.title}>{t.text}</PageHead>
      <SupportAccessSettings />
    </Page>
  );
}
