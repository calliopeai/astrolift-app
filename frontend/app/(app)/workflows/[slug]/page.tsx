import { redirect } from "next/navigation";

export default async function WorkflowDetailIndexPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  // /workflows/[slug] is the detail shell, not a destination — land on
  // Build, the default BROCS pillar.
  const { slug } = await params;
  redirect(`/workflows/${encodeURIComponent(slug)}/build`);
}
