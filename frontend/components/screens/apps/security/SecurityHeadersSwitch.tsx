"use client";

import { ShieldCheckIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AstroliftAppAccess } from "@/graphql/__generated__/schema";

import type { useSecurityHeadersSwitch } from "./use-security-headers-switch";

export interface SecurityHeadersSwitchViewProps {
  access: Pick<AstroliftAppAccess, "appSlug" | "securityHeaders">;
  /** The switch, for an operator holding `app.access`; read-only without it. */
  control?: ReturnType<typeof useSecurityHeadersSwitch>;
}

/**
 * Whether the edge adds a fixed set of security headers to every response
 * from the app's hosts. Off by default; neither turning it on nor off asks
 * for confirmation. Only ingress-nginx clusters honour it.
 */
export function SecurityHeadersSwitchView({ access, control }: SecurityHeadersSwitchViewProps) {
  const t = useTranslations("apps.security.securityHeaders");
  const { securityHeaders } = access;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex items-center gap-2 text-sm font-medium">
        <ShieldCheckIcon className="size-4" />
        {t("title")}
        <Badge variant={securityHeaders ? "default" : "secondary"} className="text-2xs font-normal">
          {securityHeaders ? t("on") : t("off")}
        </Badge>
      </div>
      <p className="text-muted-foreground text-xs">{t("description")}</p>
      {control && (
        <div className="flex flex-col items-start gap-1.5">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={control.saving}
            onClick={() => void control.onSet(!securityHeaders)}
          >
            {securityHeaders ? t("disable") : t("enable")}
          </Button>
          {control.error && (
            <p role="alert" className="text-destructive text-xs">
              {control.error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
