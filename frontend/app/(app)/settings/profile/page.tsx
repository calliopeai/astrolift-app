"use client";

import { useTheme } from "next-themes";
import * as React from "react";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { PageShell } from "@/components/PageShell";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export default function ProfileSettingsPage() {
  const { theme, setTheme } = useTheme();

  return (
    <PageShell
      title="Profile"
      description="Personal preferences for your sign-in. Stored client-side until SCIM sync writes them server-side."
    >
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Appearance</CardTitle>
            <CardDescription>
              Choose how the dashboard looks. Defaults to your system preference.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 max-w-sm">
            <Label htmlFor="theme">Theme</Label>
            <Select value={theme ?? "system"} onValueChange={setTheme}>
              <SelectTrigger id="theme">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="system">System</SelectItem>
                <SelectItem value="light">Light</SelectItem>
                <SelectItem value="dark">Dark</SelectItem>
              </SelectContent>
            </Select>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Language</CardTitle>
            <CardDescription>
              Surface language. Falls back to en when a translation is missing.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <LanguageSwitcher />
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}
