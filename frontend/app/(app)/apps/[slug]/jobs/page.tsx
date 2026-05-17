import { PreloadQuery } from "@/lib/apollo";
import {
  LIST_COMMAND_RUNS,
  LIST_SCHEDULED_JOB_RUNS,
} from "@/graphql/lifecycle/lifecycle.queries";

import { JobsClient } from "@/app/(app)/jobs/jobs-client";

import { AppTabs } from "../components/app-tabs";

export const metadata = { title: "Jobs · App · Astrolift" };

export default async function AppJobsPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  return (
    <PreloadQuery
      query={LIST_SCHEDULED_JOB_RUNS}
      variables={{ appSlug: slug, limit: 100 }}
    >
      <PreloadQuery
        query={LIST_COMMAND_RUNS}
        variables={{ appSlug: slug, limit: 100 }}
      >
        <JobsClient
          appSlug={slug}
          tabs={<AppTabs slug={slug} active="deployments" />}
        />
      </PreloadQuery>
    </PreloadQuery>
  );
}
