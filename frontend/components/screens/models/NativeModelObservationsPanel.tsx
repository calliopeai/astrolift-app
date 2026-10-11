"use client";

import { useTranslations } from "next-intl";
import { DefinitionList } from "@/components/ui/definition-list";
import { Section } from "@/components/ui/section";

export function NativeModelObservationsPanel() {
  const t = useTranslations("models.native.observations");
  return (
    <Section id="model-metrics" title={t("title")} description={t("description")}>
      <DefinitionList
        items={[
          { term: t("inference"), description: t("inferenceUnknown") },
          { term: t("traffic"), description: t("trafficUnsupported") },
          { term: t("tokensCost"), description: t("tokensCostUnsupported") },
        ]}
      />
    </Section>
  );
}
