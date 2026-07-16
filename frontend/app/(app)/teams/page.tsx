import { redirect } from "next/navigation";

export const metadata = {
  title: "Teams · Astrolift",
};

/**
 * The canonical org-teams surface lives at /administration/teams. This
 * /teams list page is a thin server alias that forwards query params
 * (e.g. ?team=<slug> emitted by the workspace nav tree) so deep links
 * keep working. The /teams/[slug] detail page stays canonical here.
 */
export default async function TeamsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (typeof value === "string" && value !== "") qs.append(key, value);
  }
  const query = qs.toString();
  redirect(query ? `/administration/teams?${query}` : "/administration/teams");
}
