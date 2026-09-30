"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { copyWithFeedback } from "@/lib/copy-with-feedback";

import { GENERATE_INSTALL_ENROLLMENT_QR } from "@/graphql/identity/identity.mutations";
import type {
  AstroliftEnrollmentQrPayload,
  MutationResult,
} from "@/graphql/identity/identity.types";

type QrPayload = AstroliftEnrollmentQrPayload;

/**
 * Mobile-device pairing (#494): mints a server-issued enrollment QR and
 * copies its URI or payload. The data half of PairDeviceView.
 */
export function usePairDevice() {
  const t = useTranslations("settings.security.devices");
  const [payload, setPayload] = React.useState<QrPayload | null>(null);

  const [generate, { loading }] = useMutation<{
    generateInstallEnrollmentQr: MutationResult<QrPayload>;
  }>(GENERATE_INSTALL_ENROLLMENT_QR);

  async function onMint(label: string): Promise<void> {
    const { data } = await generate({
      variables: { input: { label: label.trim() || null, ttlSeconds: 300 } },
    });
    const res = data?.generateInstallEnrollmentQr;
    if (res?.ok && res.data) {
      setPayload(res.data);
    } else {
      toast.error(res?.errors?.[0]?.message ?? t("toastGenerateFailed"));
    }
  }

  async function onCopyVerificationUri() {
    if (!payload) return;
    await copyWithFeedback(payload.verificationUri, t("toastUriCopied"), t("toastCopyFailed"));
  }

  async function onCopyPayload() {
    if (!payload) return;
    await copyWithFeedback(payload.qrPayload, t("toastPayloadCopied"), t("toastCopyFailed"));
  }

  function onClear() {
    setPayload(null);
  }

  return { payload, loading, onMint, onCopyVerificationUri, onCopyPayload, onClear };
}
