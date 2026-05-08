import { PreloadQuery } from "@/lib/apollo";
import {
  LIST_COMMAND_RUNS,
  LIST_SCHEDULED_JOB_RUNS,
} from "@/graphql/lifecycle/lifecycle.queries";

import { JobsClient } from "./jobs-client";

export const metadata = { title: "Jobs · Astrolift" };

export default function JobsPage() {
  return (
    <PreloadQuery query={LIST_SCHEDULED_JOB_RUNS} variables={{ limit: 100 }}>
      <PreloadQuery query={LIST_COMMAND_RUNS} variables={{ limit: 100 }}>
        <JobsClient />
      </PreloadQuery>
    </PreloadQuery>
  );
}
