import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";

/** The root 404 (app/not-found.tsx). */
export function NotFoundScreen() {
  const t = useTranslations("errorScreens");
  return (
    <div className="bg-background flex min-h-screen items-center justify-center">
      <div className="flex max-w-md flex-col items-center gap-6 px-4 text-center">
        <div className="flex items-center gap-4">
          <span className="text-4xl font-bold tracking-tight">404</span>
          <Separator
            orientation="vertical"
            className="h-10 data-vertical:h-10 data-vertical:self-auto"
          />
          <span className="text-muted-foreground text-lg">{t("notFound")}</span>
        </div>

        <p className="text-muted-foreground text-sm">{t("notFoundDescription")}</p>

        <Button asChild>
          <Link href="/">{t("goHome")}</Link>
        </Button>
      </div>
    </div>
  );
}
