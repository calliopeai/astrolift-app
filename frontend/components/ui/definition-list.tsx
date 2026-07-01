import * as React from "react";

import { cn } from "@/lib/utils";

export interface DefinitionListItem {
  /** The key (rendered muted). */
  term: React.ReactNode;
  /** The value. */
  description: React.ReactNode;
}

interface DefinitionListProps extends Omit<React.ComponentProps<"dl">, "children"> {
  items: DefinitionListItem[];
  /**
   * `"row"` (default) — term left, value right, hairline-divided rows.
   * `"stack"` — term above value; better for narrow columns / long values.
   */
  orientation?: "row" | "stack";
}

/**
 * Key/value metadata as a semantic `<dl>`.
 *
 * Replaces the mini-cards that were used for object metadata (region,
 * created-at, owner, …) — those added a container depth and washed out in
 * dark mode. A `DefinitionList` lives at the leaf of the container rule,
 * *inside* a `Card` or `Section`; it never wraps one.
 *
 * @example
 * <DefinitionList
 *   items={[
 *     { term: "Region", description: "us-west-2" },
 *     { term: "Created", description: <time dateTime={iso}>{human}</time> },
 *   ]}
 * />
 */
export function DefinitionList({
  items,
  orientation = "row",
  className,
  ...props
}: DefinitionListProps) {
  return (
    <dl
      className={cn(
        "text-sm",
        orientation === "row" ? "divide-border divide-y" : "flex flex-col gap-3",
        className
      )}
      {...props}
    >
      {items.map((item, i) => (
        <div
          key={i}
          className={cn(
            orientation === "row"
              ? "grid grid-cols-[minmax(0,10rem)_1fr] items-baseline gap-4 py-2 first:pt-0 last:pb-0"
              : "flex flex-col gap-0.5"
          )}
        >
          <dt className="text-muted-foreground">{item.term}</dt>
          <dd className="text-foreground min-w-0 break-words">{item.description}</dd>
        </div>
      ))}
    </dl>
  );
}
