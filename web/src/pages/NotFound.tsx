import { Compass } from "lucide-react";
import { Link } from "react-router";
import { Page } from "@/components/Shell";
import { Empty } from "@/components/ui/basics";

export function NotFoundPage() {
  return (
    <Page>
      <div className="card">
        <Empty icon={<Compass />} title="This page does not exist" action={<Link className="btn primary" to="/" viewTransition>Back to instances</Link>}>
          The link may be old, or the instance was deleted.
        </Empty>
      </div>
    </Page>
  );
}
