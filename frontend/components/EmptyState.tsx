import Link from "next/link";

import { Button } from "@/components/ui/button";

interface EmptyStateProps {
  icon: React.ReactNode;
  title: string;
  description?: string;
  actionHref?: string;
  actionLabel?: string;
  secondary?: React.ReactNode;
}

export function EmptyState({
  icon,
  title,
  description,
  actionHref,
  actionLabel,
  secondary,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-md border border-dashed py-12 px-6 text-center">
      <div className="bg-muted text-muted-foreground rounded-md p-2">{icon}</div>
      <div>
        <p className="font-medium">{title}</p>
        {description && (
          <p className="text-muted-foreground mx-auto mt-1 max-w-sm text-sm leading-relaxed">
            {description}
          </p>
        )}
      </div>
      {actionHref && actionLabel && (
        <Button asChild size="sm">
          <Link href={actionHref}>{actionLabel}</Link>
        </Button>
      )}
      {secondary}
    </div>
  );
}
