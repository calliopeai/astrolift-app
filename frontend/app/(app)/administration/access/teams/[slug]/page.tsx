import { redirect } from "next/navigation";
import { TEAMS_HREF } from "@/components/screens/administration/access/access-nav";
import { routeTitle } from "@/lib/i18n/route-title";

export const generateMetadata = routeTitle("team");

/** Team membership is the first-class team landing; Access has its own tab. */
export default async function TeamPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  redirect(`${TEAMS_HREF}/${encodeURIComponent(decodeURIComponent(slug))}/members`);
}
