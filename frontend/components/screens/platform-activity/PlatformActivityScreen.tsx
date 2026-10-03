"use client";

import { useTranslations } from "next-intl";

import {
  WorkflowInstancesScreen,
  type WorkflowInstancesScreenProps,
} from "@/components/screens/workflows/list/WorkflowInstancesScreen";

export function PlatformActivityScreen(props: WorkflowInstancesScreenProps) {
  const t = useTranslations("home.panels");
  const title = t("platform-activity");
  return <WorkflowInstancesScreen {...props} title={title} crumbs={[{ label: title }]} />;
}
