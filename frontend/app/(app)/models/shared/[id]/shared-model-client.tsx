"use client";
import { useState } from "react";
import { modelSourceMode } from "@/components/screens/models/native-model-source";
import { NativeModelObservationsPanel } from "@/components/screens/models/NativeModelObservationsPanel";
import { NativeModelSettingsClient } from "@/components/screens/models/NativeModelSettingsClient";
import { ModelConnectionPolicyClient } from "@/components/screens/models/ModelConnectionPolicyClient";
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
  const [removalConfirmed, setRemovalConfirmed] = useState(false);
  const mode = props.model ? modelSourceMode(props.model) : "unsupported";
  return (
    <SharedModelDetailScreen
      {...props}
      removalConfirmed={removalConfirmed}
      subscriptions={
        props.model && mode !== "unsupported" ? (
          <ModelSubscriptionsClient
            model={props.model}
            blocked={props.stale || !!props.error}
            onRefreshDeployment={props.onRetry}
          />
        ) : null
      }
      observations={
        props.model && mode === "hosted" ? (
          <ModelObservationsClient model={props.model} />
        ) : props.model && (mode === "native" || mode === "native_unavailable") ? (
          <NativeModelObservationsPanel />
        ) : null
      }
      prompt={
        props.model && mode === "hosted" ? <SharedModelPromptClient model={props.model} /> : null
      }
      management={
        props.model && mode !== "unsupported" ? (
          <>
            {mode === "native" || mode === "native_unavailable" ? (
              <NativeModelSettingsClient
                model={props.model}
                blocked={props.stale || !!props.error}
                onRefresh={props.onRetry}
                onRemoved={() => setRemovalConfirmed(true)}
              />
            ) : mode === "hosted" ? (
              <SharedModelManagementClient
                model={props.model}
                blocked={props.stale || !!props.error}
                onRefresh={props.onRetry}
              />
            ) : null}
            <ModelConnectionPolicyClient
              model={props.model}
              blocked={props.stale || !!props.error}
            />
          </>
        ) : null
      }
    />
  );
}
