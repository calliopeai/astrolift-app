"use client";
import { SharedModelDetailScreen } from "@/components/screens/models/SharedModelDetailScreen";
import { useSharedModelDetail } from "@/components/screens/models/use-shared-model-detail";
import { ModelObservationsClient } from "@/components/screens/models/ModelObservationsClient";
import { SharedModelPromptClient } from "@/components/screens/models/SharedModelPromptClient";
import { ModelSubscriptionsClient } from "@/components/screens/models/ModelSubscriptionsClient";
import { SharedModelManagementClient } from "@/components/screens/models/SharedModelManagementClient";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
export function SharedModelClient({ id }: { id: string }) {
  const { org } = useActiveOrg();
  const { user } = useMe();
  return <DetailContext key={`${org?.id ?? ""}:${user?.id ?? ""}:${id}`} id={id} />;
}
function DetailContext({ id }: { id: string }) {
  const props = useSharedModelDetail(id);
  return (
    <SharedModelDetailScreen
      {...props}
      subscriptions={
        props.model ? (
          <ModelSubscriptionsClient
            model={props.model}
            blocked={props.stale || !!props.error}
            onRefreshDeployment={props.onRetry}
          />
        ) : null
      }
      observations={props.model ? <ModelObservationsClient model={props.model} /> : null}
      prompt={props.model ? <SharedModelPromptClient model={props.model} /> : null}
      management={
        props.model ? (
          <SharedModelManagementClient
            model={props.model}
            blocked={props.stale || !!props.error}
            onRefresh={props.onRetry}
          />
        ) : null
      }
    />
  );
}
