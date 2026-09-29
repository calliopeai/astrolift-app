"use client";

import { LoginScreen } from "@/components/screens/auth/LoginScreen";
import { useLogin } from "@/components/screens/auth/use-login";

export default function LoginPage() {
  return <LoginScreen {...useLogin()} />;
}
