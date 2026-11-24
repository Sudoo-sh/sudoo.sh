import { Compass } from "lucide-react";
import { Link } from "react-router";
import { Page } from "@/components/Shell";
import { Button, Empty } from "@/components/app/basics";
import { useT } from "@/i18n";
import common from "@/i18n/common";

export function NotFoundPage() {
  const t = useT(common).notFound;
  return (
    <Page>
      <div className="card">
        <Empty
          icon={<Compass />}
          title={t.title}
          action={
            <Button asChild variant="primary">
              <Link to="/" viewTransition>
                {t.home}
              </Link>
            </Button>
          }
        >
          {t.text}
        </Empty>
      </div>
    </Page>
  );
}
