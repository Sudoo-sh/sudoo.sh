// The routes of premium features: their page when the package runs, its plan card otherwise.

import { PremiumOrNotFound, PremiumPage } from "./components";

export const TeamsRoute = () => <PremiumPage id="teams" path="/teams" />;
export const SupportAccessRoute = () => <PremiumPage id="support" path="/support-access" />;
export const ServersRoute = () => <PremiumPage id="servers" path="/servers" />;
export { PremiumOrNotFound };
