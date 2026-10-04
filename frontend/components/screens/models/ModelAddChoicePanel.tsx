"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";

export type ModelAddAction = { allowed: boolean | null; reason: string | null };
export type ModelAddChoicePanelProps = {
  hosting: ModelAddAction;
  connection: ModelAddAction;
};

export function ModelAddChoicePanel({ hosting, connection }: ModelAddChoicePanelProps) {
  const t = useTranslations("models.native.add");
  return (
    <div className="grid gap-6 md:grid-cols-2">
      {(
        [
          { key: "host", href: "/models/deploy", action: hosting },
          { key: "connect", href: "/models/connect", action: connection },
        ] as const
      ).map(({ key, href, action }) => (
        <Section key={key} title={t(`${key}Title`)} description={t(`${key}Description`)}>
          <div className="space-y-3">
            {action.allowed === true ? (
              <Button asChild>
                <Link href={href}>{t(`${key}Action`)}</Link>
              </Button>
            ) : (
              <Button disabled>{t(`${key}Action`)}</Button>
            )}
            {action.allowed !== true && (
              <p role="status" className="text-muted-foreground text-sm break-words">
                {action.reason ?? t(action.allowed === null ? "checking" : "unavailable")}
              </p>
            )}
          </div>
        </Section>
      ))}
    </div>
  );
}
