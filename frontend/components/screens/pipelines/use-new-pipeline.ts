"use client";

import { useMutation } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";
import { CREATE_PIPELINE } from "@/graphql/pipelines/pipelines.mutations";
import type { CreatePipelineInput } from "@/graphql/__generated__/schema";
import type { PipelineDraft } from "./NewPipelineScreen";

type Response = {
  createPipeline: { ok: boolean; errors: { message: string }[]; data: { id: string } | null };
};

export function useNewPipeline() {
  const router = useRouter();
  const { can } = useMyPermissions();
  const allowed = can("app.update");
  const [draft, setDraft] = useState<PipelineDraft>({
    name: "",
    repoUrl: "",
    defaultBranch: "main",
    tomlPath: "",
  });
  const [create, { loading: saving }] = useMutation<Response, { input: CreatePipelineInput }>(
    CREATE_PIPELINE
  );
  const [error, setError] = useState<string | null>(null);
  const [createdId, setCreatedId] = useState<string | null>(null);
  const busy = useRef(false);
  const committed = useRef(false);
  async function onSubmit() {
    if (
      !allowed ||
      busy.current ||
      committed.current ||
      !draft.name.trim() ||
      !draft.repoUrl.trim()
    )
      return;
    busy.current = true;
    setError(null);
    let id: string | null = null;
    try {
      const { data } = await create({
        variables: {
          input: {
            name: draft.name.trim(),
            repoUrl: draft.repoUrl.trim(),
            defaultBranch: draft.defaultBranch.trim() || "main",
            tomlPath: draft.tomlPath.trim(),
          },
        },
      });
      if (!data?.createPipeline.ok || !data.createPipeline.data)
        setError(data?.createPipeline.errors[0]?.message ?? "Could not create pipeline.");
      else {
        id = data.createPipeline.data.id;
        committed.current = true;
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create pipeline.");
    } finally {
      busy.current = false;
    }
    if (id) {
      setCreatedId(id);
      toast.success("Pipeline created");
      // Creation committed. A navigation failure must not invite a duplicate retry.
      try {
        router.push(`/pipelines/${encodeURIComponent(id)}`);
      } catch {
        toast.warning("Pipeline created. Open it using the link on this page.");
      }
    }
  }
  return {
    draft,
    onChange: (field: keyof PipelineDraft, value: string) =>
      setDraft((current) => ({ ...current, [field]: value })),
    onSubmit,
    saving,
    allowed,
    error,
    createdId,
  };
}
