"use client";

import { useMutation } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { ARCHIVE_APP, RESTORE_APP } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

export type ArchiveSource = Pick<
  AstroliftRegisteredApp,
  "id" | "slug" | "name" | "version" | "isArchived" | "archivedAt"
>;
interface ArchivePayload {
  id: string;
  slug: string;
  isArchived: boolean;
  archivedAt: string | null;
}
interface ArchiveAppResp {
  archiveApp: MutationResult<ArchivePayload>;
}
interface RestoreAppResp {
  restoreApp: MutationResult<ArchivePayload>;
}

export function useArchiveApp(appSlug: string, source?: ArchiveSource | null) {
  const t = useTranslations("apps.settings.archiveFlow");
  const router = useRouter();
  const perms = useMyPermissions();
  const allowed = (perms.loading && perms.granted.size === 0) || perms.can("app.update");
  const observed =
    source === undefined ||
    (!!source?.id && source.slug === appSlug && typeof source.isArchived === "boolean");
  const fingerprint = JSON.stringify([
    appSlug,
    source?.id,
    source?.name,
    source?.version,
    source?.isArchived,
    source?.archivedAt,
    observed,
    allowed,
  ]);
  const context = React.useMemo(() => ({ fingerprint }), [fingerprint]);
  const current = React.useRef<object | null>(context);
  React.useLayoutEffect(() => {
    current.current = context;
    return () => {
      current.current = null;
    };
  }, [context]);
  // Handle acceptance against the reviewed observation before refreshing its source.
  const [archive, { loading: archiving }] = useMutation<ArchiveAppResp>(ARCHIVE_APP, {
    fetchPolicy: "no-cache",
  });
  const [restore, { loading: restoring }] = useMutation<RestoreAppResp>(RESTORE_APP, {
    fetchPolicy: "no-cache",
  });

  async function perform(action: "archive" | "restore"): Promise<boolean> {
    if (
      current.current !== context ||
      !observed ||
      !allowed ||
      (source && source.isArchived !== (action === "restore"))
    ) {
      toast.error(t("sourceChanged"));
      return false;
    }
    const reads: { refetch: () => Promise<unknown> }[] = [];
    const options = {
      variables: { input: { appSlug } },
      onQueryUpdated: (query: { refetch: () => Promise<unknown> }) => {
        // Refresh after recording the accepted write and attempting navigation.
        reads.push(query);
        return false as const;
      },
    };
    let result: MutationResult<ArchivePayload> | null | undefined;
    try {
      if (action === "archive") {
        const { data } = await archive({
          ...options,
          refetchQueries: (reply) =>
            reply.data?.archiveApp?.ok ? [{ query: GET_APP, variables: { slug: appSlug } }] : [],
        });
        result = data?.archiveApp;
      } else {
        const { data } = await restore({
          ...options,
          refetchQueries: (reply) =>
            reply.data?.restoreApp?.ok ? [{ query: GET_APP, variables: { slug: appSlug } }] : [],
        });
        result = data?.restoreApp;
      }
    } catch (err) {
      toast.error(
        err instanceof Error && err.message
          ? err.message
          : t(action === "archive" ? "archiveFailed" : "restoreFailed")
      );
      return false;
    }
    if (!result?.ok) {
      toast.error(
        result?.errors?.[0]?.message || t(action === "archive" ? "archiveFailed" : "restoreFailed")
      );
      return false;
    }
    toast.success(
      t(action === "archive" ? "archiveAccepted" : "restoreAccepted", { slug: appSlug })
    );
    if (action === "archive" && current.current === context) {
      try {
        router.push("/apps");
      } catch {
        toast.warning(t("navigationWarning"));
      }
    }
    await Promise.all(reads.map((query) => refetchAfterMutation(query, t("refreshWarning"))));
    return true;
  }

  return {
    archiving,
    restoring,
    onArchive: () => perform("archive"),
    onRestore: async () => {
      await perform("restore");
    },
  };
}
