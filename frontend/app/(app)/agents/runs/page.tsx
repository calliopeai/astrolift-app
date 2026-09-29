import { redirect } from "next/navigation";

/** Agents › Runs lives at `/tasks` (the rail's Runs row); keep the query. */
export default async function AgentRunsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(await searchParams)) {
    for (const v of Array.isArray(value) ? value : value ? [value] : []) query.append(key, v);
  }
  const qs = query.toString();
  redirect(qs ? `/tasks?${qs}` : "/tasks");
}
