"use client";

import { Loader2Icon, ScalingIcon } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

import type { useScaleWorkload } from "./use-scale-workload";

export type ScalePopoverViewProps = ReturnType<typeof useScaleWorkload> & {
  /** Start open (stories). */
  defaultOpen?: boolean;
};

/**
 * #668 — inline scale popover so operators can bump replicas during an
 * incident without navigating to the detail page.
 */
export function ScalePopoverView({
  workloadName,
  currentDesired,
  loading,
  apply: applyValue,
  defaultOpen = false,
}: ScalePopoverViewProps) {
  const [open, setOpen] = React.useState(defaultOpen);
  const [value, setValue] = React.useState(String(currentDesired));
  function onOpenChange(next: boolean) {
    if (next) setValue(String(currentDesired));
    setOpen(next);
  }

  async function apply() {
    if (await applyValue(value)) setOpen(false);
  }

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="size-6" title={`Scale ${workloadName}`}>
          <ScalingIcon className="size-3" />
          <span className="sr-only">Scale</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-56 p-3" align="start">
        <p className="mb-2 text-xs font-medium">Scale {workloadName}</p>
        <div className="flex items-center gap-2">
          <Input
            type="number"
            min={0}
            max={50}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="h-8"
            onKeyDown={(e) => {
              if (e.key === "Enter") void apply();
              if (e.key === "Escape") setOpen(false);
            }}
            autoFocus
          />
          <Button size="sm" onClick={() => void apply()} disabled={loading}>
            {loading ? <Loader2Icon className="size-3 animate-spin" /> : "Apply"}
          </Button>
        </div>
        <p className="text-muted-foreground text-2xs mt-2">
          Current: {currentDesired}. Takes effect immediately.
        </p>
      </PopoverContent>
    </Popover>
  );
}
