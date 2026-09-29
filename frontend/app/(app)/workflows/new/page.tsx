import { NewWorkflowClient } from "./new-workflow-client";

export const metadata = { title: "New workflow · Astrolift" };

/**
 * `/workflows/new`: the stepped New workflow page (Source · Stages · Review),
 * or, with `?definition=<slug>`, the same steps configuring that definition
 * into a runnable workflow. `?pattern=` preselects a pattern.
 */
export default async function NewWorkflowPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { definition, pattern } = await searchParams;
  const first = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? null;
  return <NewWorkflowClient definitionSlug={first(definition)} pattern={first(pattern)} />;
}
