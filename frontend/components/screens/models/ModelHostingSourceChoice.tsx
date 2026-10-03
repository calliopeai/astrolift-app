"use client";

import { useId } from "react";
import { useTranslations } from "next-intl";
import { DownloadIcon, FolderOpenIcon, CheckIcon } from "lucide-react";
import { Button } from "@/components/ui/button";

export interface ModelHostingSourceChoiceProps {
  value: "huggingface" | "local";
  allowed: boolean | null;
  onChange: (value: "huggingface" | "local") => void;
}

export function ModelHostingSourceChoice(props: ModelHostingSourceChoiceProps) {
  const id = useId();
  const local = useTranslations("models.shared.localImport");
  const t = useTranslations("models.shared.hosting");
  return (
    <div role="group" aria-label={local("selectSource")} className="@container">
      <div className="grid gap-3 @md:grid-cols-2">
        {(["huggingface", "local"] as const).map((value) => {
          const selected = value === props.value;
          const Icon = value === "huggingface" ? DownloadIcon : FolderOpenIcon;
          return (
            <Button
              key={value}
              type="button"
              variant="outline"
              aria-pressed={selected}
              aria-label={local(value === "huggingface" ? "huggingFace" : "localSource")}
              aria-describedby={`${id}-${value}-help`}
              disabled={props.allowed !== true}
              onClick={() => props.onChange(value)}
              className={`bg-surface-1 h-auto items-start justify-start gap-3 p-4 text-left whitespace-normal ${selected ? "border-primary" : ""}`}
            >
              <Icon className="mt-1 size-5 shrink-0" aria-hidden="true" />
              <span className="flex-1 space-y-1">
                <span className="block font-medium">
                  {local(value === "huggingface" ? "huggingFace" : "localSource")}
                </span>
                <span
                  id={`${id}-${value}-help`}
                  className="text-muted-foreground block text-sm font-normal"
                >
                  {t(value === "huggingface" ? "huggingFaceHelp" : "localSourceHelp")}
                </span>
              </span>
              {selected && <CheckIcon className="mt-1 size-4 shrink-0" aria-hidden="true" />}
            </Button>
          );
        })}
      </div>
    </div>
  );
}
