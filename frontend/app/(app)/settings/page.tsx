import {
  BellIcon,
  BuildingIcon,
  KeyIcon,
  KeyRoundIcon,
  ScaleIcon,
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
    description: "Display name, website, retention defaults.",
  },
  {
    href: "/settings/identity-provider",
    icon: KeyRoundIcon,
    title: "Identity provider",
    description: "OIDC / SAML config + SCIM provisioning.",
  },
  {
    href: "/settings/policies",
    icon: ScaleIcon,
    title: "ABAC policies",
    description: "Runtime predicates layered on top of RBAC.",
  },
  {
    href: "/settings/profile",
    icon: UserCircleIcon,
    title: "Profile",
    description: "Theme + locale.",
  },
  {
    href: "/settings/security",
    icon: ShieldIcon,
    title: "Security",
    description: "Active sessions, MFA, admin elevation.",
  },
  {
    href: "/settings/notifications",
    icon: BellIcon,
    title: "Notifications",
    description: "Inbox of approvals, alerts, invitations.",
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
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
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
