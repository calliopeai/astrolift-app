import { WorkflowPillarPage } from "../components/workflow-pillar-page";

export const metadata = { title: "Observe · Workflow · Astrolift" };

export default async function WorkflowObservePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <WorkflowPillarPage slug={slug} pillar="observe" />;
}
