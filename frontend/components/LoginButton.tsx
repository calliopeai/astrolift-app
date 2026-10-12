"use client";

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

export default function LoginButton() {
  const t = useTranslations("authButtons");
  return (
    <Button asChild>
      <a href="/auth/login">{t("logIn")}</a>
    </Button>
  );
}
