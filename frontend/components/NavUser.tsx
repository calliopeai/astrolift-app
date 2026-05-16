"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@apollo/client/react";
import { ConnectedAccountsSection } from "@/components/ConnectedAccountsSection";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import {
  BellIcon,
  BadgeCheckIcon,
  ChevronsUpDownIcon,
  LockIcon,
  LogOutIcon,
  ZapIcon,
  AlertTriangleIcon,
  InfoIcon,
  CheckCircle2Icon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useMe } from "@/graphql/user/user.hooks";
import { GET_MY_PROFILE } from "@/graphql/identity/identity.queries";
import type { AstroliftMyProfile } from "@/graphql/identity/identity.types";
import { clearToken } from "@/lib/auth/token-store";
import { setSentryUser } from "@/lib/sentry";
import type { CurrentUser } from "@/graphql/user/user.types";

interface MyProfileResp {
  astroliftMyProfile: AstroliftMyProfile | null;
}

export const NavUser = ({ ssrUser: _ssrUser }: { ssrUser: CurrentUser | null }) => {
  const t = useTranslations("user");
  const { isMobile } = useSidebar();
  const { user } = useMe();
  const { data: profileData, loading: profileLoading } = useQuery<MyProfileResp>(
    GET_MY_PROFILE,
    { fetchPolicy: "cache-first" },
  );
  const [accountOpen, setAccountOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);

  useEffect(() => {
    setSentryUser(user ?? null);
  }, [user]);

  const profile = profileData?.astroliftMyProfile ?? null;
  const fullName =
    profile && (profile.firstName || profile.lastName)
      ? `${profile.firstName ?? ""} ${profile.lastName ?? ""}`.trim()
      : profile?.username || user?.profile?.username || "User";
  const email = profile?.email ?? "";
  const username = profile?.username ?? user?.profile?.username ?? "";
  const avatar = "";
  const initials = fullName
    .split(/\s+/)
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase() || "U";

  const handleLogout = () => {
    setSentryUser(null);
    clearToken();
    const apiRoot = process.env.NEXT_PUBLIC_API_ROOT ?? "";
    window.location.href = `${apiRoot}/app/auth1/logout`;
  };

  return (
    <>
      <SidebarMenu>
        <SidebarMenuItem>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <SidebarMenuButton
                size="lg"
                className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
              >
                <Avatar className="h-8 w-8 rounded-lg">
                  <AvatarImage src={avatar} alt={fullName} />
                  <AvatarFallback className="rounded-lg">{initials}</AvatarFallback>
                </Avatar>
                <div className="grid flex-1 text-left text-sm leading-tight">
                  <span className="truncate font-medium">{fullName}</span>
                  <span className="text-muted-foreground truncate text-xs">{email}</span>
                </div>
                <ChevronsUpDownIcon className="ml-auto size-4" />
              </SidebarMenuButton>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              className="w-(--radix-dropdown-menu-trigger-width) min-w-56 rounded-lg"
              side={isMobile ? "bottom" : "right"}
              align="end"
              sideOffset={4}
            >
              <DropdownMenuLabel className="p-0 font-normal">
                <div className="flex items-center gap-2 px-1 py-1.5 text-left text-sm">
                  <Avatar className="h-8 w-8 rounded-lg">
                    <AvatarImage src={avatar} alt={fullName} />
                    <AvatarFallback className="rounded-lg">{initials}</AvatarFallback>
                  </Avatar>
                  <div className="grid flex-1 text-left text-sm leading-tight">
                    <span className="truncate font-medium">{fullName}</span>
                    <span className="text-muted-foreground truncate text-xs">{email}</span>
                  </div>
                </div>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                <DropdownMenuItem onSelect={() => setAccountOpen(true)}>
                  <BadgeCheckIcon />
                  {t("account")}
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => setNotificationsOpen(true)}>
                  <BellIcon />
                  {t("notifications")}
                </DropdownMenuItem>
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={handleLogout}>
                <LogOutIcon />
                {t("logout")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </SidebarMenuItem>
      </SidebarMenu>

      <Sheet open={notificationsOpen} onOpenChange={setNotificationsOpen}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>{t("notifications")}</SheetTitle>
          </SheetHeader>
          <div className="flex flex-col gap-1 py-4">
            {NOTIFICATIONS.map((n) => (
              <NotificationRow key={n.id} {...n} />
            ))}
          </div>
        </SheetContent>
      </Sheet>

      <Sheet open={accountOpen} onOpenChange={setAccountOpen}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>Account</SheetTitle>
          </SheetHeader>

          <div className="flex flex-col items-center gap-3 py-6">
            <Avatar className="h-20 w-20 rounded-xl">
              <AvatarImage src={avatar} alt={fullName} />
              <AvatarFallback className="rounded-xl text-2xl">{initials}</AvatarFallback>
            </Avatar>
            <div className="text-center">
              <p className="text-lg font-semibold">{fullName}</p>
              {email && <p className="text-muted-foreground text-sm">{email}</p>}
            </div>
          </div>

          <Separator />

          <div className="flex flex-col gap-4 px-4 py-6">
            <ProfileRow
              label="Username"
              value={username || "—"}
              locked={profile?.lockedFields?.includes("username")}
            />
            <ProfileRow
              label="First name"
              value={profile?.firstName || "—"}
              locked={profile?.lockedFields?.includes("first_name")}
            />
            <ProfileRow
              label="Last name"
              value={profile?.lastName || "—"}
              locked={profile?.lockedFields?.includes("last_name")}
            />
            <ProfileRow
              label="Email"
              value={email || "—"}
              locked={profile?.lockedFields?.includes("email")}
            />
            {profile && profile.orgAllowsEdit === false && (
              <p className="bg-muted text-muted-foreground rounded-md p-3 text-xs">
                Profile editing is disabled for your organization. Names and email sync from your
                identity provider on each sign-in.
              </p>
            )}
            {profile && profile.orgAllowsEdit && (
              <Button asChild variant="outline" size="sm" className="self-start">
                <Link href="/settings/profile" onClick={() => setAccountOpen(false)}>
                  Edit profile
                </Link>
              </Button>
            )}
            {profileLoading && !profile && (
              <p className="text-muted-foreground text-sm">Loading…</p>
            )}
            <ConnectedAccountsSection />
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
};

const ProfileRow = ({
  label,
  value,
  locked,
}: {
  label: string;
  value: string;
  locked?: boolean;
}) => (
  <div className="flex items-start justify-between gap-3 text-sm">
    <span className="text-muted-foreground shrink-0">{label}</span>
    <span className="flex items-center gap-1.5 text-right">
      <span className="truncate">{value}</span>
      {locked && (
        <Badge variant="outline" className="gap-1 text-xs">
          <LockIcon className="size-3" />
          IdP-managed
        </Badge>
      )}
    </span>
  </div>
);

const Row = ({ label, value }: { label: string; value: string }) => (
  <div className="flex items-center justify-between text-sm">
    <span className="text-muted-foreground">{label}</span>
    <span className="font-mono">{value}</span>
  </div>
);

type Notification = {
  id: number;
  icon: React.ReactNode;
  title: string;
  description: string;
  time: string;
  unread: boolean;
};

const NOTIFICATIONS: Notification[] = [
  {
    id: 1,
    icon: <ZapIcon className="text-primary h-4 w-4" />,
    title: "Quantum model released",
    description:
      "The new Quantum model is now available with 1M-token context and extended thinking.",
    time: "2 min ago",
    unread: true,
  },
  {
    id: 2,
    icon: <AlertTriangleIcon className="h-4 w-4 text-yellow-500" />,
    title: "Usage threshold reached",
    description: "You have used 80% of your monthly token quota. Consider upgrading your plan.",
    time: "1 hr ago",
    unread: true,
  },
  {
    id: 3,
    icon: <CheckCircle2Icon className="h-4 w-4 text-green-500" />,
    title: "Fine-tuning job complete",
    description: "Your fine-tuning job 'customer-support-v2' finished successfully.",
    time: "3 hr ago",
    unread: true,
  },
  {
    id: 4,
    icon: <InfoIcon className="text-muted-foreground h-4 w-4" />,
    title: "Scheduled maintenance",
    description: "Brief downtime planned on 2026-03-01 between 02:00–03:00 UTC.",
    time: "Yesterday",
    unread: false,
  },
  {
    id: 5,
    icon: <CheckCircle2Icon className="h-4 w-4 text-green-500" />,
    title: "API key rotated",
    description: "Your API key was successfully rotated. Update your integrations if needed.",
    time: "2 days ago",
    unread: false,
  },
];

const NotificationRow = ({ icon, title, description, time, unread }: Notification) => (
  <div className={`flex gap-3 rounded-lg px-3 py-3 ${unread ? "bg-accent/50" : ""}`}>
    <div className="bg-muted flex h-8 w-8 shrink-0 items-center justify-center rounded-full">
      {icon}
    </div>
    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
      <div className="flex items-center justify-between gap-2">
        <span className={`truncate text-sm ${unread ? "font-semibold" : "font-medium"}`}>
          {title}
        </span>
        {unread && <span className="bg-primary h-2 w-2 shrink-0 rounded-full" />}
      </div>
      <p className="text-muted-foreground text-xs leading-relaxed">{description}</p>
      <span className="text-muted-foreground mt-0.5 text-xs">{time}</span>
    </div>
  </div>
);
