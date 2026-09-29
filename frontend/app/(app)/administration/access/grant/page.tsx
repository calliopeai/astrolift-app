import { GrantClient } from "./grant-client";

export const metadata = { title: "Grant access · Astrolift" };

/** The one Grant access flow (access UX design 3.4), preselected from its query. */
export default function GrantPage() {
  return <GrantClient />;
}
