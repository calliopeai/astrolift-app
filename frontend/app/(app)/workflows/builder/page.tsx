import { redirect } from "next/navigation";

/** Authoring a new workflow is the stepped New workflow page now; `?pattern=` carries over. */
export default async function NewWorkflowBuilderPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { pattern } = await searchParams;
  const value = Array.isArray(pattern) ? pattern[0] : pattern;
  redirect(value ? `/workflows/new?pattern=${encodeURIComponent(value)}` : "/workflows/new");
}
