"use client";
import { useModelRuntimeSetup } from "./use-model-runtime-setup";
import { ModelRuntimeSetupPanel } from "./ModelRuntimeSetupPanel";
import { useLocalModelImport } from "./use-local-model-import";
import type { LocalModelImportProps } from "./LocalModelImportPanel";
import { LocalModelImportPanel } from "./LocalModelImportPanel";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { ModelHostingSourcePanel } from "./ModelHostingSourcePanel";
import { ModelHostingSourceChoice } from "./ModelHostingSourceChoice";
import { SharedModelDeploymentScreen } from "./SharedModelDeploymentScreen";
import { HuggingFaceCataloguePanel } from "./HuggingFaceCataloguePanel";
import { useSharedModelDeployment } from "./use-shared-model-deployment";
export function SharedModelDeploymentClient() {
  const { org } = useActiveOrg();
  const { user } = useMe();
  return <PlacementContext key={`${org?.id ?? "no-organization"}:${user?.id ?? "no-actor"}`} />;
}
function PlacementContext() {
  const {
    catalogueProps,
    hostingProps,
    onUseLocalArtifact,
    sourceKind,
    onSourceKind,
    runtimeTarget,
    refreshRuntimeAdmission,
    ...props
  } = useSharedModelDeployment();
  return (
    <SharedModelDeploymentScreen
      {...props}
      runtimeSetup={
        runtimeTarget && props.draft.computeMode ? (
          <RuntimeSetupContext
            key={`${hostingProps.scopeKey}:${runtimeTarget.clusterId}:${runtimeTarget.expectedProviderId}:${props.draft.computeMode}`}
            target={runtimeTarget}
            scopeKey={hostingProps.scopeKey}
            allowed={hostingProps.allowed === true}
            mode={props.draft.computeMode === "cpu" ? "CPU" : "GPU"}
            refreshAdmission={refreshRuntimeAdmission}
          />
        ) : null
      }
      sourceControls={
        <div className="space-y-4">
          <ModelHostingSourceChoice
            value={sourceKind}
            allowed={hostingProps.allowed}
            onChange={onSourceKind}
          />
          {sourceKind === "huggingface" && <ModelHostingSourcePanel {...hostingProps} />}
        </div>
      }
      catalogue={
        sourceKind === "huggingface" ? (
          <HuggingFaceCataloguePanel {...catalogueProps} />
        ) : (
          <LocalImportContext
            key={hostingProps.scopeKey}
            scopeKey={hostingProps.scopeKey}
            allowed={hostingProps.allowed}
            onUseArtifact={onUseLocalArtifact}
          />
        )
      }
    />
  );
}

function LocalImportContext({
  scopeKey,
  allowed,
  onUseArtifact,
}: {
  scopeKey: string;
  allowed: boolean | null;
  onUseArtifact: LocalModelImportProps["onUseArtifact"];
}) {
  const props = useLocalModelImport(scopeKey, allowed, onUseArtifact);
  return <LocalModelImportPanel {...props} />;
}

function RuntimeSetupContext({
  target,
  scopeKey,
  allowed,
  mode,
  refreshAdmission,
}: Parameters<typeof useModelRuntimeSetup> extends [infer T, infer S, infer A, infer M, infer R]
  ? { target: T; scopeKey: S; allowed: A; mode: M; refreshAdmission: R }
  : never) {
  return (
    <ModelRuntimeSetupPanel
      {...useModelRuntimeSetup(target, scopeKey, allowed, mode, refreshAdmission)}
    />
  );
}
