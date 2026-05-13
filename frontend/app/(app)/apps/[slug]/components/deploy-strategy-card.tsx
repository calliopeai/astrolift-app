"use client";

import { useMutation } from "@apollo/client/react";
import {
  GitBranchIcon,
  HandIcon,
  Loader2Icon,
  PencilIcon,
  ServerCogIcon,
  ZapIcon,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Can } from "@/components/Can";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { UPDATE_APP } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp, TriggerMode } from "@/graphql/registry/registry.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

const MODE_META: Record<
  TriggerMode,
  { label: string; icon: React.ComponentType<{ className?: string }>; hint: string }
> = {
  auto_on_push: {
    label: "Auto on push",
    icon: ZapIcon,
    hint: "Any push to the deploy branch fans out a deploy.",
  },
  manual: {
    label: "Manual only",
    icon: HandIcon,
    hint: "Deploys only when triggered from the UI or CLI.",
  },
  external_ci: {
    label: "External CI",
    icon: ServerCogIcon,
    hint: "Your CI calls Astrolift with a deploy token. No webhook.",
  },
};

interface UpdateResp {
  updateApp: MutationResult<Partial<AstroliftRegisteredApp>>;
}

interface Props {
  app: AstroliftRegisteredApp;
}

/**
 * Read view of the deploy strategy + edit affordance via a side sheet.
 * The strategy here is the trio of trigger mode, deploy branch, and
 * preview state — the three knobs that decide *when* and *from where* a
 * deploy lands. Approval policy is environment-scoped and lives on the
 * Controls section below.
 */
export function DeployStrategyCard({ app }: Props) {
  const [open, setOpen] = useState(false);
  const meta = MODE_META[app.triggerMode] ?? MODE_META.manual;
  const Icon = meta.icon;

  return (
    <section className="rounded-lg border p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
            Deploy strategy
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="gap-1.5 py-1">
              <Icon className="size-3.5 text-[var(--brand-primary)]" />
              <span className="font-medium">{meta.label}</span>
            </Badge>
            <Badge variant="secondary" className="gap-1 font-mono text-[10px]">
              <GitBranchIcon className="size-3" />
              {app.deployBranch || app.defaultBranch}
            </Badge>
            {app.previewEnabled && (
              <Badge variant="outline" className="text-[10px]">
                Preview environments
              </Badge>
            )}
          </div>
          <p className="text-muted-foreground mt-2 max-w-xl text-xs">{meta.hint}</p>
        </div>
        <Can permission="app.update">
          <Button variant="ghost" size="sm" onClick={() => setOpen(true)}>
            <PencilIcon className="size-3.5" />
            Edit
          </Button>
        </Can>
      </div>

      <EditStrategySheet app={app} open={open} onOpenChange={setOpen} />
    </section>
  );
}

function EditStrategySheet({
  app,
  open,
  onOpenChange,
}: {
  app: AstroliftRegisteredApp;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [triggerMode, setTriggerMode] = useState<TriggerMode>(app.triggerMode);
  const [deployBranch, setDeployBranch] = useState(app.deployBranch || app.defaultBranch);
  const [previewEnabled, setPreviewEnabled] = useState(app.previewEnabled);

  const [save, { loading }] = useMutation<UpdateResp>(UPDATE_APP, {
    refetchQueries: [{ query: GET_APP, variables: { slug: app.slug } }],
    awaitRefetchQueries: true,
  });

  async function handleSave() {
    const { data } = await save({
      variables: {
        input: {
          id: app.id,
          triggerMode,
          deployBranch: deployBranch.trim() || null,
          previewEnabled,
        },
      },
    });
    if (data?.updateApp.ok) {
      toast.success("Deploy strategy updated.");
      onOpenChange(false);
    } else {
      toast.error(data?.updateApp.errors?.[0]?.message ?? "Update failed.");
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full flex-col gap-4 sm:max-w-md">
        <SheetHeader>
          <SheetTitle>Edit deploy strategy</SheetTitle>
          <SheetDescription>
            Changes apply to the next deploy. In-flight rollouts complete on the previous strategy.
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-1 flex-col gap-4 px-4">
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="text-muted-foreground text-xs">Trigger mode</span>
            <select
              value={triggerMode}
              onChange={(e) => setTriggerMode(e.target.value as TriggerMode)}
              className="border-input bg-background rounded-md border px-2 py-2 text-sm"
            >
              <option value="auto_on_push">Auto on push</option>
              <option value="manual">Manual only</option>
              <option value="external_ci">External CI</option>
            </select>
          </label>

          <label className="flex flex-col gap-1.5 text-sm">
            <span className="text-muted-foreground text-xs">Deploy branch</span>
            <input
              type="text"
              value={deployBranch}
              onChange={(e) => setDeployBranch(e.target.value)}
              placeholder={app.defaultBranch}
              className="border-input bg-background rounded-md border px-2 py-2 font-mono text-sm"
            />
            <span className="text-muted-foreground text-[11px]">
              Default branch from the repo is{" "}
              <span className="font-mono">{app.defaultBranch || "main"}</span>.
            </span>
          </label>

          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={previewEnabled}
              onChange={(e) => setPreviewEnabled(e.target.checked)}
              className="mt-0.5 size-4"
            />
            <span>
              <span className="font-medium">Preview environments</span>
              <span className="text-muted-foreground block text-xs">
                Spin up an ephemeral environment per pull request.
              </span>
            </span>
          </label>
        </div>

        <SheetFooter className="flex flex-row justify-end gap-2 border-t px-4 pt-3">
          <SheetClose asChild>
            <Button variant="outline" size="sm" disabled={loading}>
              Cancel
            </Button>
          </SheetClose>
          <Button size="sm" onClick={handleSave} disabled={loading}>
            {loading && <Loader2Icon className="size-3.5 animate-spin" />}
            Save strategy
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
