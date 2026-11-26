// The Maintenance page (src/sudoo/web/v1/maintenance.py): maintenance Sudoo.sh planned on this panel, its reason and
// duration, and choosing when it runs (settings.manage); everyone who sees instances sees it.

import { MaintenanceList } from "@/components/Maintenance";
import { MAINTENANCE_TOUR } from "@/lib/tour";
import { Page, PageHead } from "@/components/Shell";
import { useT } from "@/i18n";
import words from "@/i18n/maintenance";

export function MaintenancePage() {
  const t = useT(words);
  return (
    <Page>
      <PageHead title={t.title} tour={MAINTENANCE_TOUR}>
        {t.lead}
      </PageHead>
      <MaintenanceList />
    </Page>
  );
}
