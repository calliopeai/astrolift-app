"use client";
import { useState } from "react";
import type { ClusterModelFieldsFragment } from "@/graphql/__generated__/operations";
import {
  sameNativeConnection,
  modelSourceMode,
  nativeModelFamily,
} from "@/components/screens/models/native-model-source";
import { NativeConnectionMetadataPanel } from "@/components/screens/models/NativeConnectionMetadataPanel";
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
  const [settingsReceipt, setSettingsReceipt] = useState<ClusterModelFieldsFragment | null>(null);
  const queuedConfirmed = !!(
    settingsReceipt &&
    props.model &&
    settingsReceipt.id === props.model.id &&
    settingsReceipt.organizationId === props.model.organizationId &&
    settingsReceipt.clusterId === props.model.clusterId &&
    settingsReceipt.providerId === props.model.providerId &&
    settingsReceipt.version === props.model.version &&
    sameNativeConnection(settingsReceipt, props.model)
  );
  const mode = props.model ? modelSourceMode(props.model) : "unsupported";
  const bedrock = !!props.model && nativeModelFamily(props.model) === "BEDROCK";
  return (
    <SharedModelDetailScreen
      {...props}
      removalConfirmed={removalConfirmed}
      subscriptions={
        props.model && (mode === "hosted" || (bedrock && mode !== "unsupported")) ? (
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
            {bedrock && (mode === "native" || mode === "native_unavailable") ? (
              <NativeModelSettingsClient
                model={props.model}
                blocked={props.stale || !!props.error}
                onRefresh={props.onRetry}
                onRemoved={() => setRemovalConfirmed(true)}
                onQueued={setSettingsReceipt}
                queuedConfirmed={queuedConfirmed}
              />
            ) : mode === "hosted" ? (
              <SharedModelManagementClient
                model={props.model}
                blocked={props.stale || !!props.error}
                onRefresh={props.onRetry}
              />
            ) : (
              <NativeConnectionMetadataPanel model={props.model} settings />
            )}
            {(mode === "hosted" || bedrock) && (
              <ModelConnectionPolicyClient
                model={props.model}
                blocked={props.stale || !!props.error}
              />
            )}
          </>
        ) : null
      }
    />
  );
}
