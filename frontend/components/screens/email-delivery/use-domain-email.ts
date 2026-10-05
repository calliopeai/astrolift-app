"use client";
import * as React from "react";
import { useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { useManagedDomain } from "@/components/screens/domains/use-managed-domain";
import {
  EMAIL_DELIVERY_APPS,
  EMAIL_DELIVERY_APP,
  EMAIL_DELIVERY_SERVICES,
  EMAIL_DELIVERY_SUPPORT,
} from "@/graphql/services/email-delivery.queries";
import type {
  EmailDeliveryAppsQuery,
  EmailDeliveryAppsQueryVariables,
  EmailDeliveryAppQuery,
  EmailDeliveryAppQueryVariables,
  EmailDeliveryServicesQuery,
  EmailDeliveryServicesQueryVariables,
  EmailDeliverySupportQuery,
  EmailDeliverySupportQueryVariables,
} from "@/graphql/__generated__/operations";
import type { DomainEmailScreenProps } from "./DomainEmailScreen";
export function useDomainEmail(id: string): Omit<DomainEmailScreenProps, "delivery"> {
  const t = useTranslations("emailDelivery"),
    { org, loading: orgLoading, error: orgError } = useActiveOrg(),
    { user, loading: userLoading, error: userError } = useMe();
  const domain = useManagedDomain(id);
  const ready =
    !!org?.id &&
    !!user?.id &&
    !orgLoading &&
    !userLoading &&
    !orgError &&
    !userError &&
    !!domain.domain &&
    !domain.loading &&
    !domain.error;
  const [search, setSearch] = React.useState(""),
    [app, setApp] = React.useState<DomainEmailScreenProps["app"]>(null),
    [service, setService] = React.useState<DomainEmailScreenProps["service"]>(null);
  const [appCursors, setAppCursors] = React.useState<(string | null)[]>([null]),
    [serviceCursors, setServiceCursors] = React.useState<(string | null)[]>([null]);
  const apps = useQuery<EmailDeliveryAppsQuery, EmailDeliveryAppsQueryVariables>(
    EMAIL_DELIVERY_APPS,
    {
      variables: { search: search.trim() || null, cursor: appCursors.at(-1), limit: 25 },
      skip: !ready,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const appRead = useQuery<EmailDeliveryAppQuery, EmailDeliveryAppQueryVariables>(
    EMAIL_DELIVERY_APP,
    {
      variables: { slug: app?.slug ?? "" },
      skip: !ready || !app,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const rawApp = appRead.data?.astroliftApp;
  const exactApp =
    ready &&
    !appRead.loading &&
    !appRead.error &&
    rawApp?.id === app?.id &&
    rawApp?.slug === app?.slug &&
    rawApp?.organizationSlug === org?.slug &&
    Number.isSafeInteger(rawApp?.version) &&
    rawApp!.version > 0
      ? rawApp
      : null;
  const services = useQuery<EmailDeliveryServicesQuery, EmailDeliveryServicesQueryVariables>(
    EMAIL_DELIVERY_SERVICES,
    {
      variables: { appSlug: exactApp?.slug ?? "", after: serviceCursors.at(-1), limit: 25 },
      skip: !exactApp,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const page =
    exactApp && !services.loading && !services.error
      ? services.data?.astroliftManagedServicesPage
      : null;
  const serviceRows =
    page?.items.filter((row) => row.kind === "email" && row.registeredAppSlug === exactApp?.slug) ??
    [];
  const exactService =
    service &&
    page?.items.some(
      (row) =>
        row.id === service.id && row.kind === "email" && row.registeredAppSlug === exactApp?.slug
    )
      ? (serviceRows.find((row) => row.id === service.id) ?? null)
      : null;
  const support = useQuery<EmailDeliverySupportQuery, EmailDeliverySupportQueryVariables>(
    EMAIL_DELIVERY_SUPPORT,
    {
      variables: { managedServiceId: exactService?.id ?? "" },
      skip: !exactService,
      fetchPolicy: "no-cache",
      context: { queryDeduplication: false },
    }
  );
  const sender =
    !support.loading && !support.error && support.data?.emailDeliveryTestSupport.allowed
      ? support.data.emailDeliveryTestSupport.sender
      : null;
  const senderDomain = sender?.split("@")[1]?.toLowerCase();
  function resetServices() {
    setService(null);
    setServiceCursors([null]);
  }
  return {
    domain: domain.domain,
    domainLoading: domain.loading,
    domainError: domain.error,
    apps: ready && !apps.loading && !apps.error ? (apps.data?.astroliftAppsPage.items ?? []) : [],
    appsLoading: apps.loading,
    appsError: apps.error ? t("selectionUnavailable") : null,
    appSearch: search,
    onAppSearch(value) {
      setSearch(value);
      setAppCursors([null]);
    },
    app: exactApp ? app : null,
    onApp(value) {
      setApp(
        apps.data?.astroliftAppsPage.items.find(
          (row) => row.id === value && row.organizationSlug === org?.slug
        ) ?? null
      );
      resetServices();
    },
    appsNext: !!apps.data?.astroliftAppsPage.nextCursor,
    appsPrevious: appCursors.length > 1,
    onNextApps() {
      if (apps.data?.astroliftAppsPage.nextCursor)
        setAppCursors([...appCursors, apps.data.astroliftAppsPage.nextCursor]);
    },
    onPreviousApps() {
      setAppCursors(appCursors.slice(0, -1));
    },
    services: serviceRows,
    servicesLoading: !!app && (!exactApp || services.loading),
    servicesError:
      appRead.error || services.error || (!appRead.loading && app && !exactApp)
        ? t("selectionUnavailable")
        : null,
    service: exactService,
    onService(value) {
      setService(serviceRows.find((row) => row.id === value) ?? null);
    },
    servicesNext: !!page?.nextCursor,
    servicesPrevious: serviceCursors.length > 1,
    onNextServices() {
      if (page?.nextCursor) {
        setService(null);
        setServiceCursors([...serviceCursors, page.nextCursor]);
      }
    },
    onPreviousServices() {
      setService(null);
      setServiceCursors(serviceCursors.slice(0, -1));
    },
    onRefresh() {
      domain.onRetry();
      if (ready) void apps.refetch().catch(() => {});
      if (app) void appRead.refetch().catch(() => {});
      if (exactApp) void services.refetch().catch(() => {});
    },
    association: senderDomain
      ? senderDomain === domain.domain?.zone.toLowerCase() ||
        senderDomain.endsWith(`.${domain.domain?.zone.toLowerCase()}`)
        ? "match"
        : "different"
      : "unconfirmedDomain",
    probe: domain.probe,
    probeLoading: domain.probeLoading,
    probeError: domain.probeError,
    onProbe: domain.onProbe,
    onResetProbe: domain.onResetProbe,
  };
}
