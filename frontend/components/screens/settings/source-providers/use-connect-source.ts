"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type { ConnectSourceInput } from "@/graphql/__generated__/schema";
import { CONNECT_SOURCE } from "@/graphql/scm/scm.mutations";
import { LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import type { AstroliftSourceConnection, MutationResult } from "@/graphql/scm/scm.types";

interface MutationResp {
  connectSource: MutationResult<AstroliftSourceConnection>;
}

/**
 * The generic "Connect a source host" mutation. The data half of
 * ConnectSourceDialogView; the view owns the form.
 */
export function useConnectSource() {
  const t = useTranslations("sourceProviders.toasts");
  const [connect, { loading }] = useMutation<MutationResp>(CONNECT_SOURCE, {
    refetchQueries: [{ query: LIST_SOURCE_CONNECTIONS }],
    awaitRefetchQueries: true,
  });

  /** Resolves true when the connection was stored (close the sheet). */
  async function onConnect(input: ConnectSourceInput): Promise<boolean> {
    const { data } = await connect({ variables: { input } });
    if (data?.connectSource.ok) {
      toast.success(t("connected", { name: data.connectSource.data?.name ?? input.kind }));
      return true;
    }
    toast.error(data?.connectSource.errors?.[0]?.message ?? t("connectFailed"));
    return false;
  }

  return { loading, onConnect };
}
