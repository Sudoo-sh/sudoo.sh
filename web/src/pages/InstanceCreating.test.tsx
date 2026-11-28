import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// the words read the chosen language from the page when they load: no page here
vi.mock("@/i18n", async () => {
  const common = (await import("@/i18n/common")).default;
  const instance = (await import("@/i18n/instance")).default;
  return {
    useT: (d: unknown) => (d === common ? common.en : instance.en),
    words: (d: unknown) => (d === common ? common.en : instance.en),
  };
});
vi.mock("@tanstack/react-query", () => ({ useQueryClient: () => ({ invalidateQueries: () => {} }) }));
vi.mock("@/api/queries", () => ({
  keys: { instance: (n: string) => ["instance", n] },
  useJob: () => ({ data: undefined }),
  useJobs: () => ({ data: [], isPending: false }),
}));
vi.mock("@/components/Shell", () => ({
  Page: ({ children }: { children: unknown }) => <main>{children as never}</main>,
  useCan: () => () => true,
}));
vi.mock("@/components/InstanceCard", () => ({ Monogram: () => null }));
vi.mock("@/components/JobDrawer", () => ({ JobSteps: () => null }));
vi.mock("@/components/WhyItFailed", () => ({ WhyItFailed: () => null }));
vi.mock("./NotFound", () => ({ NotFoundPage: () => <p>This page does not exist</p> }));

import { ApiError } from "@/api/client";
import { MissingInstance } from "./InstanceCreating";

const render = (error: unknown) =>
  renderToStaticMarkup(<MissingInstance name="sales-main" error={error} onRetry={() => {}} />);

describe("MissingInstance", () => {
  it("says the page does not exist only when the panel answered 404", () => {
    expect(render(new ApiError(404, "There is no such instance."))).toContain("This page does not exist");
  });

  it("says the instance could not be loaded, with a retry, when the panel is busy or down", () => {
    for (const error of [
      new ApiError(503, "The panel is busy. Try again in a few seconds."),
      new ApiError(500, "Something unexpected went wrong on the server."),
      new ApiError(0, ""),
    ]) {
      const html = render(error);
      expect(html).not.toContain("This page does not exist");
      expect(html).toContain("This could not be loaded");
      expect(html).toContain("Try again");
    }
    expect(render(new ApiError(503, "The panel is busy. Try again in a few seconds."))).toContain("The panel is busy");
  });
});
