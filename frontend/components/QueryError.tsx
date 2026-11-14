"use client";
import { ErrorState } from "@calliopelabs/ui";

export interface QueryErrorProps {
  title: string;
  error?: string | { message: string } | null;
  onRetry?: () => void;
  retryLabel?: string;
}

/**
 * The library ErrorState, inline, rendered only while there is an error.
 * Call sites pass a query's error unconditionally and rely on this rendering
 * nothing when it is empty; ErrorState itself always renders.
 */
export const QueryError = ({ error, ...props }: QueryErrorProps) => {
  const message = typeof error === "string" ? error : error?.message;
  if (!message) return null;
  return <ErrorState error={message} {...props} />;
};
