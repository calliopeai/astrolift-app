import { CheckCircle2Icon, MailIcon, XCircleIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import type { useInvitationAccept } from "./use-invitation-accept";

export type InvitationAcceptProps = ReturnType<typeof useInvitationAccept>;

/** Accept an org invitation: the explanation, then the outcome. */
export function InvitationAccept({
  loading,
  ok,
  errorMessage,
  onAccept,
  onGoToDashboard,
}: InvitationAcceptProps) {
  const t = useTranslations("invitationAccept");
  return (
    <div className="bg-muted/30 flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <MailIcon className="size-5" />
            {t("title")}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {ok ? (
            <>
              <div className="bg-success/10 border-success-border rounded-md border p-3 text-sm">
                <div className="flex items-center gap-2 font-medium">
                  <CheckCircle2Icon className="size-4" /> {t("successTitle")}
                </div>
                <p className="text-muted-foreground mt-1">{t("successBody")}</p>
              </div>
              <Button onClick={onGoToDashboard} className="w-full">
                {t("goToDashboard")}
              </Button>
            </>
          ) : errorMessage ? (
            <>
              <div className="bg-danger/10 border-danger-border rounded-md border p-3 text-sm">
                <div className="flex items-center gap-2 font-medium">
                  <XCircleIcon className="size-4" /> {t("errorTitle")}
                </div>
                <p className="text-muted-foreground mt-1">{errorMessage}</p>
              </div>
              <Button variant="outline" onClick={onGoToDashboard} className="w-full">
                {t("continue")}
              </Button>
            </>
          ) : (
            <>
              <p className="text-muted-foreground text-sm">{t("intro")}</p>
              <p className="text-muted-foreground text-xs">{t("tokenHelp")}</p>
              <Button onClick={onAccept} disabled={loading} className="w-full">
                {t("title")}
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
