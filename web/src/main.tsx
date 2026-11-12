import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "./styles/main.scss";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";
import { ApiError } from "./api/client";
import { keys } from "./api/queries";
import { ToastProvider } from "./components/ui/overlay";
import { applyTheme, storedTheme } from "./lib/theme";
import { router } from "./router";

applyTheme(storedTheme());

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

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
