import { LoginShell } from "@/components/screens/auth/LoginShell";

export default function LoginLayout({ children }: { children: React.ReactNode }) {
  return <LoginShell>{children}</LoginShell>;
}
