"use client";

import type * as React from "react";

import { Label } from "@/components/ui/label";

/**
 * One field of the create wizard and the graduation form: its label, the
 * control, and one line under it: the server's refusal when there is one,
 * else the field's own problem, else its help. Pure.
 */
export function WizardField({
  id,
  label,
  required = false,
  help,
  problem,
  serverError,
  children,
}: {
  id: string;
  label: string;
  required?: boolean;
  help?: React.ReactNode;
  /** The field's own check, already in words. */
  problem?: string | null;
  /** What the server said about this field, verbatim. */
  serverError?: string | null;
  children: React.ReactNode;
}) {
  const error = serverError || problem;
  return (
    <div className="min-w-0 space-y-2">
      <Label htmlFor={id}>
        {label}
        {required && (
          <span className="text-destructive" aria-hidden>
            {" "}
            *
          </span>
        )}
      </Label>
      {children}
      {error ? (
        <p
          id={`${id}-note`}
          role="alert"
          className="text-destructive text-xs [overflow-wrap:anywhere]"
        >
          {error}
        </p>
      ) : help ? (
        <p id={`${id}-note`} className="text-muted-foreground text-xs">
          {help}
        </p>
      ) : null}
    </div>
  );
}
