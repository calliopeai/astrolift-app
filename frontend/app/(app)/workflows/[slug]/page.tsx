import { BuilderContent } from "./components/builder-content";

export const metadata = { title: "Builder · Workflow · Astrolift" };

/** The Builder tab, at the workflow's root (spec 44 §5.2); `/build` and `/builder` redirect here. */
export default function WorkflowBuilderPage() {
  return <BuilderContent />;
}
