import { routeTitle } from "@/lib/i18n/route-title";

import { TriggersContent } from "../components/triggers-content";

export const generateMetadata = routeTitle("triggers", "workflow");

/** The Triggers tab (spec 44 §5.2): the trigger, its schedule, and whether it is enabled. */
export default function WorkflowTriggersPage() {
  return <TriggersContent />;
}
