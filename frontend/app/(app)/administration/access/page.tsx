import { redirect } from "next/navigation";

import { PEOPLE_HREF } from "@/components/screens/administration/access/access-nav";

export const metadata = { title: "Access · Astrolift" };

/** Admin › Access lands on People (access UX design 3.1). */
export default function AccessPage() {
  redirect(PEOPLE_HREF);
}
