"use client";

import { useTranslations } from "next-intl";
import { Section } from "@/components/ui/section";
import { DefinitionList } from "@/components/ui/definition-list";
import type { ClusterModelFieldsFragment } from "@/graphql/__generated__/operations";
import { modelSourceMode, nativeModelFamily } from "./native-model-source";

/** Read-only common metadata. Future family shapes do not enable adoption. */
export function NativeConnectionMetadataPanel({
  model,
  settings = false,
}: {
  model: ClusterModelFieldsFragment;
  settings?: boolean;
}) {
  const t = useTranslations("models.native.common");
  const details = useTranslations("models.native.details");
  const inventory = useTranslations("models.shared.inventory");
  const family = nativeModelFamily(model) ?? "UNKNOWN";
  const configured = modelSourceMode(model) === "native";
  const connection = model.nativeConnection;
  return (
    <Section
      id={settings ? "model-settings" : undefined}
      aria-label={settings ? inventory("settings") : t("title")}
      title={settings ? inventory("settings") : t("title")}
      description={configured ? t("identityHelp") : t("unavailable")}
    >
      <DefinitionList
        items={[
          { term: t("family"), description: t(family) },
          {
            term: details("configuration"),
            description: details(configured ? "configured" : "unavailable"),
          },
          ...(configured
            ? [
                {
                  term: t("resourceIdentity"),
                  description: (
                    <code className="break-all">{connection?.resourceIdentityFingerprint}</code>
                  ),
                },
                {
                  term: t("reviewedSource"),
                  description: (
                    <code className="break-all">{connection?.reviewedSourceFingerprint}</code>
                  ),
                },
              ]
            : []),
        ]}
      />
      <p className="text-muted-foreground mt-4">
        {t(
          family === "VERTEX"
            ? "vertexHelp"
            : family === "FOUNDRY"
              ? "foundryHelp"
              : "accessUnknown"
        )}
      </p>
    </Section>
  );
}
