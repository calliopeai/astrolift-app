"use client";

import {
  BellIcon,
  BookOpenIcon,
  DownloadIcon,
  KeyRoundIcon,
  LogOutIcon,
  RotateCcwIcon,
  ShieldIcon,
  UserIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

/**
 * The signed-in person's menu, at the foot of the main rail. Pure: name,
 * email and sign-out come from the shell. Every item is a page, one home per
 * destination (spec 31 §10); the settings no longer also open as side sheets.
 */
export interface UserMenuProps {
  fullName: string;
  email: string;
  onSignOut: () => void;
  collapsed?: boolean;
}

function initialsOf(name: string): string {
  return (
    name
      .split(/\s+/)
      .map((part) => part[0])
      .filter(Boolean)
      .slice(0, 2)
      .join("")
      .toUpperCase() || "U"
  );
}

export function UserMenu({ fullName, email, onSignOut, collapsed = false }: UserMenuProps) {
  const t = useTranslations("user");
  const links = [
    { href: "/settings/profile", label: t("profile"), icon: UserIcon },
    { href: "/settings/security", label: t("security"), icon: ShieldIcon },
    { href: "/settings/notifications", label: t("notifications"), icon: BellIcon },
    { href: "/tokens", label: t("apiTokens"), icon: KeyRoundIcon },
    { href: "/dashboard?onboarding=1", label: t("rerunOnboarding"), icon: RotateCcwIcon },
  ];
  const help = [
    { href: "/documentation", label: t("documentation"), icon: BookOpenIcon },
    { href: "/downloads", label: t("downloads"), icon: DownloadIcon },
  ];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={`${fullName}: account menu`}
        className={cn(
          "hover:bg-sidebar-accent/60 focus-visible:ring-ring flex h-10 w-full min-w-0 items-center gap-2 rounded-md px-1.5 text-left focus-visible:ring-2 focus-visible:outline-none",
          collapsed && "justify-center px-0"
        )}
      >
        <Avatar className="size-7 rounded-md">
          <AvatarFallback className="rounded-md text-xs">{initialsOf(fullName)}</AvatarFallback>
        </Avatar>
        {!collapsed && (
          <span className="grid min-w-0 flex-1 leading-tight">
            <span className="truncate text-sm font-medium">{fullName}</span>
            <span className="text-muted-foreground truncate text-xs">{email}</span>
          </span>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent side="right" align="end" className="min-w-56">
        <DropdownMenuLabel className="grid font-normal">
          <span className="truncate text-sm font-medium">{fullName}</span>
          <span className="text-muted-foreground truncate text-xs">{email}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          {links.map((l) => (
            <DropdownMenuItem key={l.href} asChild>
              <Link href={l.href}>
                <l.icon aria-hidden /> {l.label}
              </Link>
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          {help.map((l) => (
            <DropdownMenuItem key={l.href} asChild>
              <Link href={l.href}>
                <l.icon aria-hidden /> {l.label}
              </Link>
            </DropdownMenuItem>
          ))}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={onSignOut}>
          <LogOutIcon aria-hidden /> {t("logout")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
