import {
  BellIcon,
  BuildingIcon,
  GitBranchIcon,
  KeyIcon,
  KeyRoundIcon,
  ScaleIcon,
  ShieldIcon,
  UserCircleIcon,
} from "lucide-react";
import { getTranslations } from "next-intl/server";
import Link from "next/link";

import { PageShell } from "@/components/PageShell";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type SectionKey =
  | "organization"
  | "identityProvider"
  | "sourceProviders"
  | "policies"
  | "profile"
  | "security"
  | "notifications"
  | "apiTokens";

const sections: { key: SectionKey; href: string; icon: typeof BellIcon }[] = [
  { key: "organization", href: "/settings/organization", icon: BuildingIcon },
  { key: "identityProvider", href: "/settings/identity-provider", icon: KeyRoundIcon },
  { key: "sourceProviders", href: "/settings/source-providers", icon: GitBranchIcon },
  { key: "policies", href: "/settings/policies", icon: ScaleIcon },
  { key: "profile", href: "/settings/profile", icon: UserCircleIcon },
  { key: "security", href: "/settings/security", icon: ShieldIcon },
  { key: "notifications", href: "/settings/notifications", icon: BellIcon },
  { key: "apiTokens", href: "/tokens", icon: KeyIcon },
];

export const metadata = { title: "Settings · Astrolift" };

export default async function SettingsPage() {
  const t = await getTranslations("settingsIndex");
  return (
    <PageShell title={t("title")} description={t("description")}>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {sections.map((s) => (
          <Link key={s.href} href={s.href} className="contents">
            <Card className="hover:bg-accent/40 transition-colors">
              <CardHeader className="flex flex-row items-start gap-3 space-y-0">
                <div className="bg-primary/10 text-primary rounded-md p-2">
                  <s.icon className="size-4" />
                </div>
                <div>
                  <CardTitle className="text-base">
                    {t(`sections.${s.key}.title`)}
                  </CardTitle>
                </div>
              </CardHeader>
              <CardContent>
                <CardDescription>
                  {t(`sections.${s.key}.description`)}
                </CardDescription>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </PageShell>
  );
}
