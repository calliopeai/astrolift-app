"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type { MyStepUpPinQuery } from "@/graphql/__generated__/operations";
import { CLEAR_STEP_UP_PIN, SET_STEP_UP_PIN } from "@/graphql/identity/identity.mutations";
import { MY_STEP_UP_PIN } from "@/graphql/identity/identity.queries";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { waitForElevation } from "@/lib/auth/step-up-events";

export type StepUpPinStatus = NonNullable<MyStepUpPinQuery["myStepUpPin"]>;

/**
 * The viewer's step-up PIN (#2399). Setting one needs a fresh SSO or
 * password step-up: the refusal opens the step-up prompt through the Apollo
 * afterware, and a password step-up retries the set here. An SSO step-up
 * leaves the page, so the PIN is typed again on return. The data half of
 * StepUpPinView.
 */
export function useStepUpPin() {
  const t = useTranslations("settings.security.stepUpPin");
  const { data, loading } = useQuery<MyStepUpPinQuery>(MY_STEP_UP_PIN);
  // The step-up prompt reads the methods from the elevation status, so a
  // new or cleared PIN refreshes it too.
  const refetchQueries = [{ query: MY_STEP_UP_PIN }, "GetElevationStatus"];

  const [setPin, { loading: setting }] = useMutation<{
    setStepUpPin: MutationResult<{ replaced: boolean; status: StepUpPinStatus }>;
  }>(SET_STEP_UP_PIN, { refetchQueries });
  const [clearPin, { loading: clearing }] = useMutation<{
    clearStepUpPin: MutationResult<StepUpPinStatus>;
  }>(CLEAR_STEP_UP_PIN, { refetchQueries });

  /** True when the PIN was stored. */
  async function onSet(pin: string): Promise<boolean> {
    for (let attempt = 0; attempt < 2; attempt++) {
      const { data: res } = await setPin({ variables: { input: { pin } } });
      const out = res?.setStepUpPin;
      if (out?.ok && out.data) {
        toast.success(out.data.replaced ? t("toastChanged") : t("toastSet"));
        return true;
      }
      const error = out?.errors?.[0];
      if (error?.code === "STEP_UP_REQUIRED" && attempt === 0) {
        if (await waitForElevation()) continue;
        return false;
      }
      toast.error(error?.message ?? t("toastFailed"));
      return false;
    }
    return false;
  }

  async function onClear(): Promise<void> {
    const { data: res } = await clearPin();
    if (res?.clearStepUpPin.ok) toast.success(t("toastCleared"));
    else toast.error(res?.clearStepUpPin.errors?.[0]?.message ?? t("toastFailed"));
  }

  return {
    status: data?.myStepUpPin ?? null,
    loading,
    saving: setting || clearing,
    onSet,
    onClear,
  };
}
