import Image from "next/image";

export interface AuthSplashProps {
  message: string;
  detail?: string;
  tone?: "default" | "warning" | "destructive";
}

/** Logo, wordmark and one status line: the in-between states of the auth flow. */
export function AuthSplash({ message, detail, tone = "default" }: AuthSplashProps) {
  const toneClass =
    tone === "destructive"
      ? "text-destructive"
      : tone === "warning"
        ? "text-warning-fg"
        : "text-muted-foreground";
  return (
    <div className="flex flex-col items-center gap-4">
      <Image src="/logo.svg" alt="Astrolift" width={48} height={48} priority />
      <div className="text-foreground text-lg font-semibold tracking-tight">Astrolift</div>
      <div className={`${toneClass} text-sm`}>{message}</div>
      {detail && <div className="text-muted-foreground max-w-md text-center text-xs">{detail}</div>}
    </div>
  );
}
