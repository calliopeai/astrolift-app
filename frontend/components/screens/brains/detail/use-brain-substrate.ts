"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import type {
  UpdateBrainSubstrateMutation,
  UpdateBrainSubstrateMutationVariables,
} from "@/graphql/__generated__/operations";
import { UPDATE_BRAIN_SUBSTRATE } from "@/graphql/registry/brains.mutations";

import type { SubstrateServerError } from "../BrainSubstrateForm";
import { gatedWrite } from "../gated-write";
import {
  draftFromSubstrate,
  heldPersistence,
  substrateInput,
  type SubstrateDraft,
} from "../substrate-cells";
import type { BrainSubstrateTabProps } from "./BrainSubstrateTab";
import { useBrainEnvironmentStatus } from "./use-brain-overview";
import type { FramedBrainValue } from "./use-brain-frame";

/**
 * The data half of the Substrate tab (#2427): the status read in the picked
 * environment (adoption and graduation come with it), and graduation
 * through `updateBrainSubstrate`, which commits the new cell to the deploy
 * branch and applies it; its push deploys it.
 */
export function useBrainSubstrate(framed: FramedBrainValue): BrainSubstrateTabProps {
  const t = useTranslations("brains.substrateTab");
  const { environment, environments, status, onRetry } = useBrainEnvironmentStatus(framed);
  const current =
    (status.state === "ready" ? status.status?.substrate : null) ?? framed.brain.substrate ?? null;
  const [open, setOpen] = React.useState(false);
  const [draft, setDraft] = React.useState<SubstrateDraft>(() => draftFromSubstrate(current));
  const [error, setError] = React.useState<SubstrateServerError | null>(null);
  const locked = heldPersistence(current?.persistence ?? []);
  const [update, updateState] = useMutation<
    UpdateBrainSubstrateMutation,
    UpdateBrainSubstrateMutationVariables
  >(UPDATE_BRAIN_SUBSTRATE, { refetchQueries: ["GetBrain", "GetBrainStatus"] });

  return {
    local: framed.brain.substrate ?? null,
    status,
    environment,
    environments,
    onRetry,
    graduateNeeds: !framed.can.share ? "app.update" : !framed.can.rebuild ? "app.deploy" : null,
    graduation: {
      open,
      onOpen: () => {
        setDraft(draftFromSubstrate(current));
        setError(null);
        setOpen(true);
      },
      onClose: () => setOpen(false),
      draft,
      onChange: (next) => {
        setDraft(next);
        setError(null);
      },
      locked,
      submitting: updateState.loading,
      onSubmit: async () => {
        const outcome = await gatedWrite(
          async () =>
            (
              await update({
                variables: {
                  input: { slug: framed.brain.slug, substrate: substrateInput(draft, { locked }) },
                },
              })
            ).data?.updateBrainSubstrate,
          t("graduateFailed")
        );
        if (outcome.ok) {
          setOpen(false);
          toast.success(t("graduated", { slug: framed.brain.slug }));
        } else setError({ field: outcome.field, message: outcome.message });
      },
      error,
    },
  };
}
