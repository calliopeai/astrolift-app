import * as React from "react";

import { cn } from "@/lib/utils";

interface SectionProps extends Omit<React.ComponentProps<"section">, "title"> {
  /** Region heading. Rendered as a semantic heading, not inside a border. */
  title?: React.ReactNode;
  /** Supporting copy under the title. */
  description?: React.ReactNode;
  /** Right-aligned actions (buttons, filters) beside the title. */
  action?: React.ReactNode;
  /** Draw a hairline divider beneath the heading. Default: spacing only. */
  divided?: boolean;
  /** Heading level for correct document outline. Default: `"h2"`. */
  headingLevel?: "h2" | "h3";
  children: React.ReactNode;
}

/**
 * A page region — expressed as a heading, not a border.
 *
 * `Section` is the top level of the container rule
 * `PageShell > Section > (Card | Table | Chart)` (max **2** container
 * depths). It replaces the `<section className="rounded-lg border …">`
 * idiom that produced "boxes inside boxes" and read badly in dark mode.
 * A `Section` groups content with spacing (and an optional hairline
 * divider) — never a filled or bordered box. Put bounded objects in a
 * `Card` *inside* the section; never nest a `Section` inside a `Card`.
 *
 * @example
 * <Section title="Deployments" action={<Button size="sm">Deploy</Button>}>
 *   <DataTable columns={columns} data={rows} />
 * </Section>
 */
export function Section({
  title,
  description,
  action,
  divided = false,
  headingLevel = "h2",
  className,
  children,
  ...props
}: SectionProps) {
  const Heading = headingLevel;
  const hasHeader = title || description || action;

  return (
    <section className={cn("flex min-w-0 flex-col gap-4", className)} {...props}>
      {hasHeader && (
        <div
          className={cn(
            "flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between",
            divided && "border-b pb-3"
          )}
        >
          <div className="flex min-w-0 flex-col gap-0.5">
            {title && <Heading className="text-base font-semibold tracking-tight">{title}</Heading>}
            {description && <p className="text-muted-foreground text-sm">{description}</p>}
          </div>
          {action && <div className="flex flex-wrap items-center gap-2">{action}</div>}
        </div>
      )}
      {children}
    </section>
  );
}
