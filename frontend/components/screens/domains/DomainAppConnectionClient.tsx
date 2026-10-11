"use client";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import {
  DomainAppConnectionSheet,
  type DomainAppConnectionSheetProps,
} from "./DomainAppConnectionSheet";
import { useDomainAppRoute, type DomainAppRouteContext } from "./use-domain-app-route";

export function DomainAppConnectionEntry(props: DomainAppConnectionSheetProps) {
  const t = useTranslations("domainAppRouting");
  return (
    <>
      <Button onClick={() => props.onOpenChange(true)}>{t("connect")}</Button>
      <DomainAppConnectionSheet {...props} />
    </>
  );
}
function CurrentConnection({ context }: { context: DomainAppRouteContext }) {
  return <DomainAppConnectionEntry {...useDomainAppRoute(context)} />;
}
/** Staged private client. Mount only after actual backend dependency composition. */
export function DomainAppConnectionClient({ context }: { context: DomainAppRouteContext }) {
  return (
    <CurrentConnection
      key={`${context.actor}:${context.organization}:${context.domainId}`}
      context={context}
    />
  );
}
