"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

import { MODEL_CATALOG, gpuFit, neededGiB } from "./model-catalog";
import type { useDeployModel } from "./use-deploy-model";

const CUSTOM = "__custom__";
const INSTALL_DEFAULT = "__default__";

export type DeployModelSheetViewProps = ReturnType<typeof useDeployModel> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeployed: () => void;
  /** Preselected environment (stories); the app starts with none. */
  initialEnvId?: string;
};

/** The Deploy model sheet: host an open-weight model with vLLM (#2040). */
export function DeployModelSheetView({
  open,
  onOpenChange,
  onDeployed,
  envs,
  clusters,
  loading,
  deploy,
  initialEnvId = "",
}: DeployModelSheetViewProps) {
  const [envId, setEnvId] = React.useState(initialEnvId);
  const [catalogId, setCatalogId] = React.useState(MODEL_CATALOG[0].id);
  const [customId, setCustomId] = React.useState("");
  const [customParams, setCustomParams] = React.useState("");
  const [gpus, setGpus] = React.useState(1);
  const [frontend, setFrontend] = React.useState(INSTALL_DEFAULT);
  const [hfRef, setHfRef] = React.useState("");

  const env = envs.find((e) => e.id === envId);
  const custom = catalogId === CUSTOM;
  const modelId = custom ? customId.trim() : catalogId;
  const paramsB = custom
    ? Number(customParams) || 0
    : (MODEL_CATALOG.find((m) => m.id === catalogId)?.paramsB ?? 0);
  const capabilities = clusters.find((c) => c.id === env?.clusterId)?.capabilities;
  const fit =
    env && paramsB ? gpuFit(capabilities, gpus, neededGiB(paramsB)) : { kind: "unknown" as const };
  const blocked = fit.kind === "noNode" || fit.kind === "tooSmall";

  async function submit() {
    if (!env || !modelId) return;
    const config: Record<string, unknown> = { model: modelId, gpu: gpus };
    if (frontend !== INSTALL_DEFAULT) config.frontend = frontend;
    if (hfRef.trim()) config.hf_token_secret_ref = hfRef.trim();
    if (await deploy({ env, modelId, config })) {
      onOpenChange(false);
      onDeployed();
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="overflow-y-auto">
        <SheetHeader>
          <SheetTitle>Deploy a model</SheetTitle>
          <SheetDescription>
            Hosts an open-weight model with vLLM on the environment&apos;s cluster. The app binds to
            it through MODEL_ENDPOINT_URL, MODEL_API_KEY and MODEL_DEPLOYMENT_NAME.
          </SheetDescription>
        </SheetHeader>

        <div className="grid gap-4 px-4">
          <div className="grid gap-1.5">
            <Label htmlFor="model-env">App environment</Label>
            <Select value={envId} onValueChange={setEnvId}>
              <SelectTrigger id="model-env" aria-label="App environment">
                <SelectValue placeholder="Choose where it runs" />
              </SelectTrigger>
              <SelectContent>
                {envs.map((e) => (
                  <SelectItem key={e.id} value={e.id}>
                    {e.registeredAppSlug} · {e.name}
                    {e.clusterSlug ? ` (${e.clusterSlug})` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="model-id">Model</Label>
            <Select value={catalogId} onValueChange={setCatalogId}>
              <SelectTrigger id="model-id" aria-label="Model">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MODEL_CATALOG.map((m) => (
                  <SelectItem key={m.id} value={m.id}>
                    {m.label} · {neededGiB(m.paramsB)} GB
                  </SelectItem>
                ))}
                <SelectItem value={CUSTOM}>Custom Hugging Face id</SelectItem>
              </SelectContent>
            </Select>
            {custom && (
              <div className="grid grid-cols-[1fr_7rem] gap-2">
                <Input
                  aria-label="Hugging Face model id"
                  placeholder="org/model"
                  value={customId}
                  onChange={(e) => setCustomId(e.target.value)}
                />
                <Input
                  aria-label="Parameters (billions)"
                  placeholder="params, B"
                  inputMode="decimal"
                  value={customParams}
                  onChange={(e) => setCustomParams(e.target.value)}
                />
              </div>
            )}
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="grid gap-1.5">
              <Label htmlFor="model-gpus">GPUs</Label>
              <Input
                id="model-gpus"
                type="number"
                min={0}
                max={16}
                value={gpus}
                onChange={(e) => setGpus(Math.max(0, Math.min(16, Number(e.target.value) || 0)))}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="model-frontend">vLLM frontend</Label>
              <Select value={frontend} onValueChange={setFrontend}>
                <SelectTrigger id="model-frontend" aria-label="vLLM frontend">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={INSTALL_DEFAULT}>Cluster default</SelectItem>
                  <SelectItem value="rust">Rust</SelectItem>
                  <SelectItem value="python">Python</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="model-hf">Hugging Face token (secret ref, optional)</Label>
            <Input
              id="model-hf"
              placeholder="services/<org>/<owner>/hf#token"
              value={hfRef}
              onChange={(e) => setHfRef(e.target.value)}
            />
          </div>

          <p
            role="status"
            className={blocked ? "text-destructive text-sm" : "text-muted-foreground text-sm"}
          >
            {fit.kind === "fits" &&
              `Fits: needs about ${neededGiB(paramsB)} GB across ${gpus} GPU(s) of ${fit.perGpuGiB} GB.`}
            {fit.kind === "tooSmall" &&
              `Needs about ${fit.needGiB} GB but ${gpus} GPU(s) of ${fit.perGpuGiB} GB hold ${fit.perGpuGiB * gpus} GB. Add GPUs or pick a smaller model.`}
            {fit.kind === "noNode" &&
              `No node of this cluster has ${gpus} GPUs; the most on one node is ${fit.most}.`}
            {fit.kind === "unknown" &&
              (env
                ? "GPU memory unknown for this cluster; the deploy is checked when it schedules."
                : "")}
          </p>
        </div>

        <SheetFooter>
          <Button onClick={submit} disabled={!env || !modelId || blocked || loading}>
            {loading ? "Deploying..." : "Deploy"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
