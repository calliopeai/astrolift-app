"use client";

import { useTranslations } from "next-intl";

import { Button } from "@calliopelabs/ui";

export default function LoginButton() {
  const t = useTranslations("authButtons");
  return (
    <Button asChild>
      <a href="/auth/login">{t("logIn")}</a>
    </Button>
  );
}
