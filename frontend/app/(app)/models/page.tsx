import { ModelsClient } from "./models-client";
export const metadata = { title: "Models · Astrolift" };
/** Shared cluster deployments lead; existing app/project endpoints have a separate view. */
export default function ModelsPage() {
  return <ModelsClient />;
}
