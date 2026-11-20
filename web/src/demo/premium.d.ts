// The premium features' compiled web files the demo was built with (vite.config.ts, demoPremium): per package, its
// manifest's web part and the SHA-256 of each file. Empty when the build had none.
declare module "virtual:sudoo-demo-premium" {
  import type { PremiumRoute, PremiumSection } from "@/premium/types";

  const packages: Record<
    string,
    { version: string | null; routes: PremiumRoute[]; sections: PremiumSection[]; styles: string[]; strings: string | null; files: Record<string, string> }
  >;
  export default packages;
}
