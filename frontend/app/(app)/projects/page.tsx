import { redirect } from "next/navigation";

export const metadata = {
  title: "Projects · Astrolift",
};

/**
 * The canonical org-projects surface lives at /administration/projects.
 * This /projects list page is a thin server alias that forwards query
 * params (e.g. ?team=<slug>&new=1 emitted by the workspace nav tree's
 * "Add project" affordance) so deep links keep working. The
 * /projects/[slug] detail page stays canonical here.
 */
export default async function ProjectsPage({
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
  redirect(query ? `/administration/projects?${query}` : "/administration/projects");
}
