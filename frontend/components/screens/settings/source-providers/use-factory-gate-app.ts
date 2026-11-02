"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { ROTATE_FACTORY_GATE_APP_KEY } from "@/graphql/scm/scm.mutations";
import { GET_FACTORY_GATE_APP } from "@/graphql/scm/scm.queries";
import type { AstroliftFactoryGateApp, MutationResult } from "@/graphql/scm/scm.types";

interface FactoryGateAppResp {
  astroliftFactoryGateApp: AstroliftFactoryGateApp;
}

interface RotateResp {
  rotateFactoryGateAppKey: MutationResult<AstroliftFactoryGateApp>;
}

// Where the manifest flow sends the browser back: this section, on the Source tab.
const RETURN_TO = "/providers?section=factory-gate#source";

/**
 * The factory gate App (#2589): its state, and the hand-off to auth1's
 * manifest flow, which registers it or, once registered, installs it. The data
 * half of FactoryGateAppView.
 */
export function useFactoryGateApp() {
  const query = useQuery<FactoryGateAppResp>(GET_FACTORY_GATE_APP, {
    fetchPolicy: "cache-and-network",
  });
  const data = query.data ?? query.previousData;

  /** Register the App on `orgLogin`, or install the one registered there. */
  function onRegister(orgLogin: string) {
    const params = new URLSearchParams({
      purpose: "factory_gate",
      org: orgLogin,
      return_to: RETURN_TO,
    });
    window.location.href = `/app/auth1/scm/github/app-manifest/start?${params.toString()}`;
  }

  return {
    app: data?.astroliftFactoryGateApp ?? null,
    loading: query.loading && !data,
    error: query.error && !data ? { message: query.error.message } : null,
    onRetry: () => {
      void query.refetch();
    },
    onRegister,
  };
}

export type FactoryGateAppData = ReturnType<typeof useFactoryGateApp>;

/** The rotate key mutation. The data half of RotateFactoryGateKeySheet. */
export function useRotateFactoryGateKey() {
  const t = useTranslations("sourceProviders.factoryGate.toasts");
  const [rotate, { loading }] = useMutation<RotateResp>(ROTATE_FACTORY_GATE_APP_KEY, {
    refetchQueries: [{ query: GET_FACTORY_GATE_APP }],
    awaitRefetchQueries: true,
  });

  /** Resolves true once the new key is stored; the sheet then closes. */
  async function handleRotate(privateKeyPem: string): Promise<boolean> {
    const { data } = await rotate({ variables: { input: { privateKeyPem } } });
    const result = data?.rotateFactoryGateAppKey;
    if (result?.ok) {
      toast.success(t("rotated"));
      return true;
    }
    toast.error(result?.errors?.[0]?.message ?? t("rotateFailed"));
    return false;
  }

  return { rotating: loading, rotate: handleRotate };
}

export type RotateFactoryGateKeyData = ReturnType<typeof useRotateFactoryGateKey>;
