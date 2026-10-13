import { routeTitle } from "@/lib/i18n/route-title";

import { BuilderContent } from "./components/builder-content";

export const generateMetadata = routeTitle("builder", "workflow");

/** The Builder tab, at the workflow's root (spec 44 §5.2); `/build` and `/builder` redirect here. */
export default function WorkflowBuilderPage() {
  return <BuilderContent />;
}
