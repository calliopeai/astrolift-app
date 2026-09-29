import { CommandRunsClient } from "./command-runs-client";

export const metadata = { title: "Command runs · Astrolift" };

/** One-off command runs across apps, cursor paged. */
export default function CommandRunsPage() {
  return <CommandRunsClient />;
}
