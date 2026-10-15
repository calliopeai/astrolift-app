"use client";

import { CopyIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { useFormatters } from "@/lib/i18n/formatters";

import { type BrainConnection, brainMcpConfig, brainMcpUrl } from "./brain-connection";

function CopyBlock({ label, value }: { label: string; value: string }) {
  const t = useTranslations("brains.connectDialog");
  return (
    <div className="space-y-1">
      <div className="text-muted-foreground text-xs font-medium">{label}</div>
      <div className="bg-muted relative rounded p-3 pr-20 font-mono text-xs break-all whitespace-pre-wrap">
        {value}
        <Button
          size="sm"
          variant="ghost"
          className="absolute top-1 right-1 h-7"
          aria-label={t("copyLabel", { label })}
          onClick={() => {
            void navigator.clipboard.writeText(value);
            toast.success(t("copied"));
          }}
        >
          <CopyIcon className="size-3" />
          {t("copy")}
        </Button>
      </div>
    </div>
  );
}

/**
 * The one-time view of a brain read token (#2320): the MCP URL and a client
 * config to paste. The token is held only while this is open; closing it
 * drops it, and a new one is a new Connect.
 */
export function ConnectBrainDialog({
  connection,
  onDismiss,
}: {
  connection: BrainConnection | null;
  onDismiss: () => void;
}) {
  const t = useTranslations("brains.connectDialog");
  const fmt = useFormatters();
  return (
    <AlertDialog open={connection !== null} onOpenChange={(o) => !o && onDismiss()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("title", { slug: connection?.slug ?? "" })}</AlertDialogTitle>
          <AlertDialogDescription>
            {connection
              ? t("description", { expiresAt: fmt.formatDateTime(connection.expiresAt) })
              : null}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {connection && (
          <div className="space-y-3 py-2">
            {connection.endpoints.map((endpoint) => (
              <CopyBlock key={endpoint} label={t("mcpUrl")} value={brainMcpUrl(endpoint)} />
            ))}
            <CopyBlock label={t("config")} value={brainMcpConfig(connection)} />
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogAction onClick={onDismiss}>{t("done")}</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
