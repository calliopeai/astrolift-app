import { TriggersContent } from "../components/triggers-content";

export const metadata = { title: "Triggers · Workflow · Astrolift" };

/** The Triggers tab (spec 44 §5.2): the trigger, its schedule, and whether it is enabled. */
export default function WorkflowTriggersPage() {
  return <TriggersContent />;
}
