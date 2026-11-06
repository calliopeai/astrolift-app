"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { gatedWrite } from "@/components/screens/brains/gated-write";
import type { AstroliftAppAccess } from "@/graphql/__generated__/schema";
import type {
  SetAppSecurityHeadersMutation,
  SetAppSecurityHeadersMutationVariables,
} from "@/graphql/__generated__/operations";
import { GET_APP_ACCESS, SET_APP_SECURITY_HEADERS } from "@/graphql/registry/registry.queries";

/**
 * The security headers switch: the app's hosts answer with a fixed set of
 * security headers added at the edge. Not a step-up action, so the write
 * is sent once; a refusal shows under the button. The data half of
 * SecurityHeadersSwitchView; mounted only for an operator holding `app.access`.
 */
export function useSecurityHeadersSwitch(access: AstroliftAppAccess) {
  const t = useTranslations("apps.security.securityHeaders");
  const [error, setError] = React.useState<string | null>(null);
  const [send, { loading: saving }] = useMutation<
    SetAppSecurityHeadersMutation,
    SetAppSecurityHeadersMutationVariables
  >(SET_APP_SECURITY_HEADERS, {
    refetchQueries: [{ query: GET_APP_ACCESS, variables: { appSlug: access.appSlug } }],
  });

  /** Resolves false on a refusal, which the view shows under the switch. */
  async function onSet(enabled: boolean): Promise<boolean> {
    const outcome = await gatedWrite(
      async () =>
        (await send({ variables: { input: { appSlug: access.appSlug, enabled } } })).data
          ?.setAppSecurityHeaders,
      t("saveFailed")
    );
    if (!outcome.ok) {
      setError(outcome.message);
      return false;
    }
    setError(null);
    toast.success(enabled ? t("enabled") : t("disabled"));
    return true;
  }

  return { error, saving, onSet };
}
