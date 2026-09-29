"use client";

import { useMutation } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { ARCHIVE_APP, RESTORE_APP } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";

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

/**
 * Archive (scale every workload to zero, suppress deploys) and restore.
 * A successful archive navigates back to /apps.
 */
export function useArchiveApp(appSlug: string) {
  const router = useRouter();
  const refetch = [{ query: GET_APP, variables: { slug: appSlug } }];
  const [archive, { loading: archiving }] = useMutation<ArchiveAppResp>(ARCHIVE_APP, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });
  const [restore, { loading: restoring }] = useMutation<RestoreAppResp>(RESTORE_APP, {
    refetchQueries: refetch,
    awaitRefetchQueries: true,
  });

  /** Resolves true when the app was archived (close the dialog). */
  async function onArchive(): Promise<boolean> {
    try {
      const { data } = await archive({ variables: { input: { appSlug } } });
      const env = data?.archiveApp;
      if (!env?.ok) {
        toast.error(env?.errors?.[0]?.message ?? "Archive failed.");
        return false;
      }
      toast.success("App archived — workloads scaled to zero.");
      router.push("/apps");
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Archive failed.");
      return false;
    }
  }

  async function onRestore() {
    try {
      const { data } = await restore({ variables: { input: { appSlug } } });
      const env = data?.restoreApp;
      if (!env?.ok) {
        toast.error(env?.errors?.[0]?.message ?? "Restore failed.");
        return;
      }
      toast.success("App restored — workloads returning to pre-archive replicas.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Restore failed.");
    }
  }

  return { archiving, restoring, onArchive, onRestore };
}
