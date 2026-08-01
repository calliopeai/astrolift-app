import { redirect } from "next/navigation";

/**
 * The Console tab split into Observe › Logs and Control › Shell (#1247).
 * Its logs half is what deep links point at — PodExpander's "open in console"
 * link, bookmarks, URLs pasted into incident threads — so `/console` keeps
 * resolving and lands on Logs with the query string intact.
 */
export default async function AppConsoleRedirect({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { slug } = await params;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(await searchParams)) {
    if (Array.isArray(value)) value.forEach((v) => query.append(key, v));
    else if (value !== undefined) query.set(key, value);
  }
  const suffix = query.size > 0 ? `?${query}` : "";
  redirect(`/apps/${slug}/logs${suffix}`);
}
