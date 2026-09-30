"use client";

import {
  AlertCircleIcon,
  CheckCircle2Icon,
  CircleDashedIcon,
  EditIcon,
  Loader2Icon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export type StepStatus = "pending" | "running" | "done" | "failed" | "skipped";

export interface SideEffectStep {
  key: string;
  label: string;
  status: StepStatus;
  error?: string;
  note?: string;
}

export function SummaryCard({
  step,
  title,
  onJump,
  rows,
}: {
  step: 1 | 2;
  title: string;
  onJump: (step: 1 | 2) => void;
  rows: Array<{ label: string; value: string; mono?: boolean }>;
}) {
  return (
    <Card className="border-muted-foreground/20 min-w-0">
      <CardContent className="flex flex-col gap-2 p-4 text-sm">
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
            {title}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onJump(step)}
            className="text-muted-foreground hover:text-foreground -mt-1 -mr-2 h-7 gap-1 px-2 text-xs"
            aria-label={`Edit ${title}`}
          >
            <EditIcon className="size-3" />
            Edit
          </Button>
        </div>
        <dl className="grid gap-1">
          {rows.map((r) => (
            <div key={r.label} className="grid grid-cols-3 gap-2">
              <dt className="text-muted-foreground text-xs">{r.label}</dt>
              <dd
                className={cn("col-span-2 min-w-0 truncate", r.mono && "font-mono text-xs")}
                title={r.value}
              >
                {r.value || "—"}
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

export function CheckboxLocked({ checked, disabled }: { checked: boolean; disabled: boolean }) {
  return (
    <input
      type="checkbox"
      checked={checked}
      disabled={disabled}
      readOnly
      className="pointer-events-none"
      aria-disabled={disabled}
    />
  );
}

export function statusFor(effects: SideEffectStep[], key: string): StepStatus {
  return effects.find((e) => e.key === key)?.status ?? "pending";
}

export function errorFor(effects: SideEffectStep[], key: string): string | undefined {
  return effects.find((e) => e.key === key)?.error;
}

export function StatusBadge({ status, error }: { status: StepStatus; error?: string }) {
  if (status === "running") {
    return (
      <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
        <Loader2Icon className="size-3.5 animate-spin" /> Running
      </span>
    );
  }
  if (status === "done") {
    return (
      <span className="text-success-fg inline-flex items-center gap-1 text-xs">
        <CheckCircle2Icon className="size-3.5" /> Done
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="text-destructive inline-flex items-center gap-1 text-xs" title={error}>
        <AlertCircleIcon className="size-3.5" /> Failed
      </span>
    );
  }
  if (status === "skipped") {
    return (
      <Badge variant="outline" className="text-2xs">
        Skipped
      </Badge>
    );
  }
  return (
    <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
      <CircleDashedIcon className="size-3.5" /> Pending
    </span>
  );
}
