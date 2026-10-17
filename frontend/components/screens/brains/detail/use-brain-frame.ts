"use client";

import { CombinedGraphQLErrors } from "@apollo/client";
import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { usePathname } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import type {
  GetBrainArchiveStateQuery,
  GetBrainArchiveStateQueryVariables,
  GetBrainQuery,
  GetBrainQueryVariables,
  IssueBrainReadTokenMutation,
  IssueBrainReadTokenMutationVariables,
} from "@/graphql/__generated__/operations";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { ISSUE_BRAIN_READ_TOKEN } from "@/graphql/registry/brains.mutations";
import { GET_BRAIN, GET_BRAIN_ARCHIVE_STATE } from "@/graphql/registry/brains.queries";
import { RESTORE_APP } from "@/graphql/registry/registry.mutations";
import { useAppCan } from "@/lib/permissions/app-permissions";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import type { BrainConnection } from "../brain-connection";
import { BRAIN_KINDS, type BrainCan, type FramedBrain } from "./brain-detail-model";
import type { BrainFrameProps } from "./BrainFrame";

interface RestoreAppResp {
  restoreApp: MutationResult<{ id: string; slug: string; isArchived: boolean }>;
}

/** What every tab under the frame reads: the layer, and the frame's shared actions. */
export interface FramedBrainValue {
  brain: FramedBrain;
  can: BrainCan;
  /** Read the layer again (after a share, a graduation, an archive). */
  refetch: () => Promise<unknown>;
  connecting: boolean;
  onConnect: () => void;
}

/** What the viewer may do to the framing layer; strict until its own permissions load. */
export function useBrainCan(): BrainCan {
  const update = useAppCan("app.update", { optimistic: false });
  const deploy = useAppCan("app.deploy", { optimistic: false });
  const remove = useAppCan("app.delete", { optimistic: false });
  const perms = useMyPermissions();
  const host = !perms.loading && perms.can("app.create");
  const readAudit = !perms.loading && perms.can("audit_log.read");
  return React.useMemo(
    () => ({
      share: update,
      shareAsEditor: update && deploy,
      rebuild: deploy,
      graduate: update && deploy,
      archive: remove,
      restore: update,
      host,
      readAudit,
    }),
    [update, deploy, remove, host, readAudit]
  );
}

/**
 * The data half of BrainFrame (#2427): one `astroliftBrain` read for the
 * header and every tab, the archived app behind a slug that no longer
 * reads as a layer (and restoreApp for it), and Connect, whose token lives
 * only in this state until the dialog closes.
 */
export function useBrainFrame(slug: string): {
  frame: Omit<BrainFrameProps, "children">;
  framed: FramedBrainValue | null;
} {
  const t = useTranslations("brains.frame");
  const pathname = usePathname() ?? `/brains/${slug}`;
  const can = useBrainCan();
  const query = useQuery<GetBrainQuery, GetBrainQueryVariables>(GET_BRAIN, {
    variables: { slug },
    fetchPolicy: "cache-and-network",
  });
  const brain = query.data?.astroliftBrain ?? null;
  const missing = Boolean(query.data) && !brain;
  const archive = useQuery<GetBrainArchiveStateQuery, GetBrainArchiveStateQueryVariables>(
    GET_BRAIN_ARCHIVE_STATE,
    { variables: { slug }, skip: !missing, fetchPolicy: "cache-and-network" }
  );
  const app = missing ? (archive.data?.astroliftApp ?? null) : null;
  const archived =
    app && app.isArchived && BRAIN_KINDS.includes(app.appKind)
      ? { name: app.name || app.slug, archivedAt: app.archivedAt ?? null }
      : null;

  const [restore, restoreState] = useMutation<RestoreAppResp>(RESTORE_APP, {
    fetchPolicy: "no-cache",
  });
  const [connection, setConnection] = React.useState<BrainConnection | null>(null);
  // Kept out of the Apollo cache, so the token is held only by this state.
  const [issue, issueState] = useMutation<
    IssueBrainReadTokenMutation,
    IssueBrainReadTokenMutationVariables
  >(ISSUE_BRAIN_READ_TOKEN, { fetchPolicy: "no-cache" });
  const onConnect = React.useCallback(async () => {
    const { data } = await issue({ variables: { slug } }).catch(() => ({ data: null }));
    const result = data?.issueBrainReadToken;
    if (result?.ok && result.data) setConnection(result.data);
    else toast.error(result?.errors[0]?.message ?? t("connectFailed"));
  }, [issue, slug, t]);

  const { refetch } = query;
  const denied =
    CombinedGraphQLErrors.is(query.error) &&
    query.error.errors.some((e) => e.extensions?.code === "PERMISSION_DENIED");
  const framed = React.useMemo<FramedBrainValue | null>(
    () =>
      brain
        ? {
            brain,
            can,
            refetch: () => refetch(),
            connecting: issueState.loading,
            onConnect: () => void onConnect(),
          }
        : null,
    [brain, can, refetch, issueState.loading, onConnect]
  );

  return {
    frame: {
      slug,
      pathname,
      brain,
      loading: (query.loading && !query.data) || (missing && archive.loading && !archive.data),
      error:
        query.error && !query.data
          ? { message: query.error.message, denied: Boolean(denied) }
          : null,
      onRetry: () => {
        void refetch().catch(() => {});
      },
      archived,
      restoring: restoreState.loading,
      onRestore: async () => {
        const { data } = await restore({ variables: { input: { appSlug: slug } } }).catch(
          (err: unknown) => {
            toast.error(err instanceof Error && err.message ? err.message : t("restoreFailed"));
            return { data: null };
          }
        );
        const result = data?.restoreApp;
        if (!result) return;
        if (!result.ok) {
          toast.error(result.errors?.[0]?.message || t("restoreFailed"));
          return;
        }
        toast.success(t("restored", { slug }));
        await Promise.all([refetch(), archive.refetch()]).catch(() => {});
      },
      can,
      connecting: issueState.loading,
      onConnect: () => void onConnect(),
      connection,
      onDismissConnection: () => setConnection(null),
    },
    framed,
  };
}
