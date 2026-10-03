"use client";
import { AlertTriangleIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
export interface QueryErrorProps {
  title: string;
  error?: string | { message: string } | null;
  onRetry?: () => void;
  retryLabel?: string;
}
export const QueryError = ({ title, error, onRetry, retryLabel = "Retry" }: QueryErrorProps) => {
  const message = typeof error === "string" ? error : error?.message;
  if (!message) return null;
  return (
    <div
      role="alert"
      className="border-danger-border bg-danger/5 flex min-w-0 flex-col gap-3 rounded-md border p-4"
    >
      <div className="flex min-w-0 items-start gap-2">
        <AlertTriangleIcon aria-hidden className="text-danger mt-0.5 size-4 shrink-0" />
        <div className="min-w-0">
          <p className="text-danger-fg text-sm font-medium">{title}</p>
          <p className="text-muted-foreground mt-1 text-xs [overflow-wrap:anywhere]">{message}</p>
        </div>
      </div>
      {onRetry && (
        <Button type="button" size="sm" variant="outline" className="w-fit" onClick={onRetry}>
          {retryLabel}
        </Button>
      )}
    </div>
  );
};
