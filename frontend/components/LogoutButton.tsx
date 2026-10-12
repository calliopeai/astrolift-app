"use client";

import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { clearToken } from "@/lib/auth/token-store";

export default function LogoutButton() {
  const t = useTranslations("authButtons");
  const handleLogout = () => {
    clearToken();
    window.location.href = "/auth/logout";
  };

  return (
    <Button variant="outline" onClick={handleLogout}>
      {t("logOut")}
    </Button>
  );
}
