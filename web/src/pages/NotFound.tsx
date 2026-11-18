import { Compass } from "lucide-react";
import { Link } from "react-router";
import { Page } from "@/components/Shell";
import { Empty } from "@/components/ui/basics";
import { useT } from "@/i18n";
import common from "@/i18n/common";

export function NotFoundPage() {
  const t = useT(common).notFound;
  return (
    <Page>
      <div className="card">
        <Empty icon={<Compass />} title={t.title} action={<Link className="btn primary" to="/" viewTransition>{t.home}</Link>}>
          {t.text}
        </Empty>
      </div>
    </Page>
  );
}
