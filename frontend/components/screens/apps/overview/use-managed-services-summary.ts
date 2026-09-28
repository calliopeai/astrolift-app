"use client";

import { useLazyQuery, useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  REVEAL_MANAGED_SERVICE_CONNECTION,
  SEND_MANAGED_SERVICE_TEST_EMAIL,
} from "@/graphql/services/services.mutations";
import {
  GET_MANAGED_SERVICE_QUEUE_DEPTH,
  LIST_MANAGED_SERVICES,
  LIST_MANAGED_SERVICE_OBJECTS,
} from "@/graphql/services/services.queries";
import type {
  AstroliftManagedService,
  AstroliftManagedServiceConnection,
  AstroliftManagedServiceObjects,
  AstroliftManagedServiceQueueDepth,
  AstroliftManagedServiceTestEmailResult,
} from "@/graphql/services/services.types";

interface Resp {
  astroliftManagedServices: AstroliftManagedService[];
}

interface RevealResp {
  revealManagedServiceConnection: MutationResult<AstroliftManagedServiceConnection>;
}

interface SendTestEmailResp {
  sendManagedServiceTestEmail: MutationResult<AstroliftManagedServiceTestEmailResult>;
}

interface ObjectsResp {
  astroliftManagedServiceObjects: AstroliftManagedServiceObjects | null;
}

interface QueueDepthResp {
  astroliftManagedServiceQueueDepth: AstroliftManagedServiceQueueDepth | null;
}

/** The managed-service fields the summary card and its dialogs read. */
export type SummaryService = Pick<
  AstroliftManagedService,
  | "id"
  | "name"
  | "kind"
  | "variant"
  | "status"
  | "statusError"
  | "environmentName"
  | "registeredAppSlug"
  | "lastActionAt"
  | "lastActionKind"
>;

/** Stable sort priority — failed first (needs attention), then in-flight,
 *  then active, then deprovisioning, then deleted/unknown. */
const STATUS_ORDER: Record<string, number> = {
  failed: 0,
  pending: 1,
  provisioning: 1,
  updating: 1,
  active: 2,
  deprovisioning: 3,
  deleted: 4,
};

/**
 * The app's bound managed services, sorted for the summary card (#401).
 * `loading` is true only for the first load; refetches render in place.
 */
export function useManagedServicesSummary(appSlug: string) {
  const { data, loading } = useQuery<Resp>(LIST_MANAGED_SERVICES, {
    variables: { appSlug, environmentName: null },
    fetchPolicy: "cache-and-network",
  });
  const services: SummaryService[] = React.useMemo(() => {
    const list = data?.astroliftManagedServices ?? [];
    return [...list].sort((a, b) => {
      const orderA = STATUS_ORDER[a.status] ?? 5;
      const orderB = STATUS_ORDER[b.status] ?? 5;
      if (orderA !== orderB) return orderA - orderB;
      return a.name.localeCompare(b.name);
    });
  }, [data]);

  return { loading: loading && !data, services };
}

/**
 * Reveal-connection data — mirrors the #424 reveal pattern. Calls the
 * audit-logged `revealManagedServiceConnection` mutation each time the
 * dialog opens and clears the envelope when it closes.
 */
export function useRevealConnection(svc: SummaryService, open: boolean) {
  const t = useTranslations("apps.settings.managedServicesSummary.revealDialog");
  const [revealed, setRevealed] = React.useState<AstroliftManagedServiceConnection | null>(null);
  const [reveal, { loading }] = useMutation<RevealResp>(REVEAL_MANAGED_SERVICE_CONNECTION, {
    refetchQueries: [
      {
        query: LIST_MANAGED_SERVICES,
        variables: { appSlug: svc.registeredAppSlug, environmentName: null },
      },
    ],
  });

  React.useEffect(() => {
    if (!open) {
      setRevealed(null);
      return;
    }
    void (async () => {
      try {
        const { data } = await reveal({
          variables: { input: { managedServiceId: svc.id } },
        });
        const env = data?.revealManagedServiceConnection;
        if (!env) {
          toast.error(t("noResponse"));
          return;
        }
        if (!env.ok) {
          toast.error(env.errors[0]?.message ?? t("failed"));
          return;
        }
        setRevealed(env.data ?? null);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t("failed"));
      }
    })();
  }, [open, reveal, svc.id, t]);

  function onCopy(value: string) {
    void navigator.clipboard.writeText(value).then(
      () => toast.success(t("copied")),
      () => toast.error(t("copyFailed"))
    );
  }

  return { revealed, loading, onCopy };
}

export interface TestEmailValues {
  recipient: string;
  subject: string;
  body: string;
}

/** Send-test-email mutation for an email managed service. */
export function useSendTestEmail(svc: SummaryService) {
  const t = useTranslations("apps.settings.managedServicesSummary.sendEmailDialog");
  const [send, { loading: sending }] = useMutation<SendTestEmailResp>(
    SEND_MANAGED_SERVICE_TEST_EMAIL
  );

  /** Resolves true when the email was sent (close the dialog). */
  async function onSend({ recipient, subject, body }: TestEmailValues): Promise<boolean> {
    if (!recipient.trim()) return false;
    try {
      const { data } = await send({
        variables: {
          input: {
            managedServiceId: svc.id,
            recipient: recipient.trim(),
            subject: subject.trim() || null,
            body: body.trim() || null,
          },
        },
      });
      const env = data?.sendManagedServiceTestEmail;
      if (!env) {
        toast.error(t("noResponse"));
        return false;
      }
      if (!env.ok) {
        toast.error(env.errors[0]?.message ?? t("failed"));
        return false;
      }
      const payload = env.data;
      if (!payload) {
        toast.error(t("noPayload"));
        return false;
      }
      toast.success(t("sent", { recipient: payload.recipient, transport: payload.transport }));
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("failed"));
      return false;
    }
  }

  return { sending, onSend };
}

/** Object-store listing, loaded when the dialog opens. */
export function useManagedServiceObjects(svc: SummaryService, open: boolean) {
  const [load, query] = useLazyQuery<ObjectsResp>(LIST_MANAGED_SERVICE_OBJECTS, {
    fetchPolicy: "cache-and-network",
  });

  React.useEffect(() => {
    if (open) {
      void load({ variables: { managedServiceId: svc.id, limit: 10 } });
    }
  }, [open, load, svc.id]);

  const result =
    (query.data?.astroliftManagedServiceObjects as AstroliftManagedServiceObjects | undefined) ??
    null;

  return {
    result,
    loading: query.loading && !result,
    onRefresh: () => {
      void load({ variables: { managedServiceId: svc.id, limit: 10 } });
    },
  };
}

/** Queue/topic depth snapshot, loaded when the dialog opens. */
export function useQueueDepth(svc: SummaryService, open: boolean) {
  const [load, query] = useLazyQuery<QueueDepthResp>(GET_MANAGED_SERVICE_QUEUE_DEPTH, {
    fetchPolicy: "cache-and-network",
  });

  React.useEffect(() => {
    if (open) {
      void load({ variables: { managedServiceId: svc.id } });
    }
  }, [open, load, svc.id]);

  const result =
    (query.data?.astroliftManagedServiceQueueDepth as
      | AstroliftManagedServiceQueueDepth
      | undefined) ?? null;

  return {
    result,
    loading: query.loading && !result,
    onRefresh: () => {
      void load({ variables: { managedServiceId: svc.id } });
    },
  };
}
