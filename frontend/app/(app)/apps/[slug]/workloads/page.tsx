import { activeSection, type SearchParams } from "@/components/screens/apps/detail/app-tabs-model";
import { AppTabSections } from "@/components/screens/apps/detail/AppTabSections";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { LIST_WORKLOADS_PAGE } from "@/graphql/registry/registry.queries";
import { LIST_MANAGED_SERVICES_PAGE } from "@/graphql/services/services.queries";
import { PreloadQuery } from "@/lib/apollo";

import { ManagedServicesClient } from "../managed-services/managed-services-client";
import { WorkloadsListClient } from "./workloads-list-client";

export const metadata = {
  title: "Workloads · Astrolift",
};

/**
 * The Workloads tab: the app's deployment, statefulset, job and cronjob
 * workloads on the embedded list, with a kind chip (spec 44 §10.2). Its
 * scheduled jobs are that list filtered to the cronjob kind (`?kind=cronjob`,
 * where `/jobs` redirects), so the Jobs section and the Workloads section
 * render the same list. Managed services are a section of the tab
 * (`?section=managed-services`, where `/managed-services` redirects). A
 * workload's own page stays a detail route under the tab.
 *
 * The list's default page (no chips, no search, by name, 25 rows) is
 * preloaded so it paints with rows; these variables have to be exactly the
 * ones `appWorkloadsVariables` sends for it, or the preload is a cache miss.
 */
export default async function AppWorkloadsPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { slug } = await params;
  const section = activeSection("workloads", await searchParams);
  return (
    <AppTabSections slug={slug} tab="workloads" active={section}>
      {section === "managed-services" ? (
        <PreloadQuery query={LIST_ENVIRONMENTS} variables={{ appSlug: slug }}>
          {/* The first page the section's list asks for (use-managed-services). */}
          <PreloadQuery
            query={LIST_MANAGED_SERVICES_PAGE}
            variables={{
              appSlug: slug,
              environmentName: null,
              search: null,
              limit: 25,
              after: null,
            }}
          >
            <ManagedServicesClient slug={slug} />
          </PreloadQuery>
        </PreloadQuery>
      ) : (
        <PreloadQuery
          query={LIST_WORKLOADS_PAGE}
          variables={{
            appSlug: slug,
            kinds: ["deployment", "statefulset", "job", "cronjob"],
            search: null,
            filter: null,
            sort: "name",
            page: 1,
            pageSize: 25,
          }}
        >
          <WorkloadsListClient slug={slug} />
        </PreloadQuery>
      )}
    </AppTabSections>
  );
}
