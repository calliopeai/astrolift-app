"use client";

import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

import type { useVncSession } from "./use-vnc-session";

export type VncSessionSectionViewProps = ReturnType<typeof useVncSession>;

/**
 * Spec-level "live session" control. When ON, the agent's environment spec runs
 * on a watchable VNC image so operators can watch the live desktop session;
 * when OFF the run is headless and streams logs instead. Shared by the agent
 * secrets dialog and the agent settings route so the toggle looks and behaves
 * identically on both. Mirrors `ManagedModelSectionView` exactly.
 */
export function VncSessionSectionView({ vncOn, vncBusy, onToggleVnc }: VncSessionSectionViewProps) {
  const t = useTranslations("agentModelAccess");
  return (
    <div className="flex items-start justify-between gap-4 rounded-md border p-3">
      <div className="space-y-1">
        <p className="text-sm font-medium">{t("vncLabel")}</p>
        <p className="text-muted-foreground text-xs">{t("vncDescription")}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <VncSessionToggle
          checked={vncOn}
          onChange={() => void onToggleVnc()}
          disabled={vncBusy}
          label={t(vncOn ? "vncDisable" : "vncEnable")}
        />
        <span
          className={cn("text-xs font-medium", vncOn ? "text-foreground" : "text-muted-foreground")}
        >
          {t(vncOn ? "on" : "off")}
        </span>
      </div>
    </div>
  );
}

/**
 * Accessible on/off switch. The repo has no shadcn/radix Switch primitive, so
 * this is a small local toggle styled with the same Tailwind tokens the rest of
 * the surface uses (mirrors the managed-model ManagedModelToggle).
 */
function VncSessionToggle({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors",
        "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-primary" : "bg-muted-foreground/30"
      )}
    >
      <span
        className={cn(
          "inline-block size-4 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5"
        )}
      />
    </button>
  );
}
