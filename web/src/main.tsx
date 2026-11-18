import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "./styles/main.scss";
import "./styles/fonts.scss";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Fragment, StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";
import { ApiError } from "./api/client";
import { keys } from "./api/queries";
import { ToastProvider } from "./components/ui/overlay";
import { isLocale, setLocale, signedOutChoice, useLocale } from "./i18n";
import { followNavigationKind } from "./lib/navigation";
import { applyTheme, storedTheme } from "./lib/theme";
import { router } from "./router";

// The demo build (npm run build:demo) answers every request in the browser, with sample data. In every other build
// this condition is false when the code is built, so the demo's module is left out entirely.
const demo = import.meta.env.MODE === "demo" ? await import("./demo") : null;
demo?.start();

applyTheme(storedTheme());
followNavigationKind(router);

const signedOut = (error: unknown) => {
  if (error instanceof ApiError && error.status === 401) client.setQueryData(keys.session, null);
};
const client: QueryClient = new QueryClient({
  queryCache: new QueryCache({ onError: signedOut }),
  mutationCache: new MutationCache({ onError: signedOut }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
});

// The language: the signed-in account's (GET /session), or before signing in the server's default when neither this
// browser nor a cookie names one (i18n/locale.ts).
client.getQueryCache().subscribe((event) => {
  if (event.type !== "updated" || event.action.type !== "success") return;
  const [key] = event.query.queryKey;
  const data = event.query.state.data as { language?: unknown; default_language?: unknown } | null | undefined;
  if (key === "session" && isLocale(data?.language)) setLocale(data.language);
  else if ((key === "setup" || key === "contact") && isLocale(data?.default_language) && !signedOutChoice())
    setLocale(data.default_language, false);
});

/** Every page drawn anew in the chosen language (state in the query cache and the toasts is kept). */
function LocaleRoot({ children }: { children: ReactNode }) {
  return <Fragment key={useLocale()}>{children}</Fragment>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <ToastProvider>
        <LocaleRoot>
          {demo && <demo.DemoBanner router={router} />}
          <RouterProvider router={router} />
        </LocaleRoot>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
