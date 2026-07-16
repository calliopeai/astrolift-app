import { WorkflowPillarPage } from "../components/workflow-pillar-page";

export const metadata = { title: "Run · Workflow · Astrolift" };

export default async function WorkflowRunPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <WorkflowPillarPage slug={slug} pillar="run" />;
}
