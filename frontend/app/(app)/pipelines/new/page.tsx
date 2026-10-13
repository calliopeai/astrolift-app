import { routeTitle } from "@/lib/i18n/route-title";

import { NewPipelineClient } from "./new-pipeline-client";
export const generateMetadata = routeTitle("newPipeline");
export default function NewPipelinePage() {
  return <NewPipelineClient />;
}
