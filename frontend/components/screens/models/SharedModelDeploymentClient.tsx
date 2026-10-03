"use client";
import { useTranslations } from "next-intl";
import { useLocalModelImport } from "./use-local-model-import";
import type { LocalModelImportProps } from "./LocalModelImportPanel";
import { LocalModelImportPanel } from "./LocalModelImportPanel";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { ModelHostingSourcePanel } from "./ModelHostingSourcePanel";
import { SharedModelDeploymentScreen } from "./SharedModelDeploymentScreen";
import { HuggingFaceCataloguePanel } from "./HuggingFaceCataloguePanel";
import { useSharedModelDeployment } from "./use-shared-model-deployment";
export function SharedModelDeploymentClient() {
  const { org } = useActiveOrg();
  const { user } = useMe();
  return <PlacementContext key={`${org?.id ?? "no-organization"}:${user?.id ?? "no-actor"}`} />;
}
function PlacementContext() {
  const t = useTranslations("models.shared.localImport");
  const { catalogueProps, hostingProps, onUseLocalArtifact, sourceKind, onSourceKind, ...props } =
    useSharedModelDeployment();
  return (
    <SharedModelDeploymentScreen
      {...props}
      sourceControls={
        <div className="space-y-4">
          <label className="flex flex-col gap-2">
            {t("selectSource")}
            <select
              value={sourceKind}
              onChange={(event) =>
                onSourceKind(event.target.value === "local" ? "local" : "huggingface")
              }
            >
              <option value="huggingface">{t("huggingFace")}</option>
              <option value="local">{t("localSource")}</option>
            </select>
          </label>
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
