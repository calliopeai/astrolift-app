"use client";

import { useParams } from "next/navigation";

import { ModelDetailScreen } from "@/components/screens/models/ModelDetailScreen";
import { useModelDetail } from "@/components/screens/models/use-model-detail";

import { ModelReplicas } from "../model-replicas";
import { ModelTestDialog } from "../model-test-dialog";

export default function ModelPage() {
  const { id } = useParams<{ id: string }>();
  const detail = useModelDetail(id);
  const model = detail.model;
  return (
    <ModelDetailScreen
      {...detail}
      replicas={
        model && (
          <ModelReplicas
            id={model.id}
            name={model.name}
            config={(model.config ?? {}) as Record<string, unknown>}
            onChanged={detail.onRetry}
          />
        )
      }
      test={model && <ModelTestDialog id={model.id} name={model.name} />}
    />
  );
}
