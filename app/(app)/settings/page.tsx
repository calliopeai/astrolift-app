import {
  BuildingIcon,
  KeyIcon,
  ShieldIcon,
  UserCircleIcon,
} from "lucide-react";
import Link from "next/link";

import { PageShell } from "@/components/PageShell";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const sections = [
  {
    href: "/settings/organization",
    icon: BuildingIcon,
    title: "Organization",
    description:
      "Display name, website, retention defaults, identity provider, JIT domains, ABAC policies.",
  },
  {
    href: "/settings/profile",
    icon: UserCircleIcon,
    title: "Profile",
    description: "Display name, locale, theme.",
  },
  {
    href: "/settings/security",
    icon: ShieldIcon,
    title: "Security",
    description: "Active sessions, MFA, API tokens.",
  },
  {
    href: "/tokens",
    icon: KeyIcon,
    title: "API tokens",
    description: "Long-lived bearer tokens for CLIs and bots.",
  },
];

export const metadata = { title: "Settings · Astrolift" };

export default function SettingsPage() {
  return (
    <PageShell
      title="Settings"
      description="Configure the platform's identity, security, and surfaces."
    >
      <div className="grid gap-4 md:grid-cols-2">
        {sections.map((s) => (
          <Link key={s.href} href={s.href} className="contents">
            <Card className="hover:bg-accent/40 transition-colors">
              <CardHeader className="flex flex-row items-start gap-3 space-y-0">
                <div className="bg-primary/10 text-primary rounded-md p-2">
                  <s.icon className="size-4" />
                </div>
                <div>
                  <CardTitle className="text-base">{s.title}</CardTitle>
                </div>
              </CardHeader>
              <CardContent>
                <CardDescription>{s.description}</CardDescription>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </PageShell>
  );
}
