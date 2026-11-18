// What moving between pages needs besides the transition itself (styles/_motion.scss): a new page starts at its top
// and going back returns to where the visitor was; a thin bar shows only when a page takes a while to load (a page
// that loads quickly shows nothing at all, so nothing flashes).

import { ScrollRestoration, useNavigation } from "react-router";
import { useT } from "@/i18n";
import common from "@/i18n/common";

export function PageMotion() {
  const loading = useNavigation().state === "loading";
  const opening = useT(common).shell.opening;
  return (
    <>
      <ScrollRestoration />
      {loading && <div className="nav-progress" role="progressbar" aria-label={opening} />}
    </>
  );
}
