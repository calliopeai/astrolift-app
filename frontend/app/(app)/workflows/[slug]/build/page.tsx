import { WorkflowPillarPage } from "../components/workflow-pillar-page";

export const metadata = { title: "Build · Workflow · Astrolift" };

export default async function WorkflowBuildPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return <WorkflowPillarPage slug={slug} pillar="build" />;
}
