"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { useTranslations } from "next-intl";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { NvkeRequestValues } from "./types";

const schema = z.object({
  address: z.string().min(1),
  port: z.number().int().min(1).max(65535),
  timeoutSeconds: z.number().int().min(30).max(86400),
});
export interface NvkeSpireServerFormProps {
  parametersText: string;
  timeoutSeconds: number;
  recovery: boolean;
  pending: boolean;
  disabled: boolean;
  onReview: (values: NvkeRequestValues) => void;
}
export function NvkeSpireServerForm(props: NvkeSpireServerFormProps) {
  const t = useTranslations("nvke");
  let body: { address?: unknown; port?: unknown } = {};
  try {
    body = JSON.parse(props.parametersText)?.body ?? {};
  } catch {
    /* An unavailable draft starts empty. */
  }
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: {
      address: !props.recovery && typeof body.address === "string" ? body.address : "",
      port: !props.recovery && typeof body.port === "number" ? body.port : 30440,
      timeoutSeconds: props.timeoutSeconds,
    },
  });
  return (
    <form
      noValidate
      className="min-w-0 space-y-4"
      onSubmit={form.handleSubmit((values) => {
        if (!props.disabled && !props.pending)
          props.onReview({
            operationKey: "updateClusterSpireServer",
            timeoutSeconds: values.timeoutSeconds,
            parametersText: JSON.stringify({
              body: { address: values.address, port: values.port },
            }),
          });
      })}
    >
      <div className="space-y-2">
        <Label htmlFor="nvke-spire-address">{t("spireAddress")}</Label>
        <Input
          id="nvke-spire-address"
          autoComplete="off"
          disabled={props.disabled || props.pending}
          {...form.register("address")}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="nvke-spire-port">{t("spirePort")}</Label>
        <Input
          id="nvke-spire-port"
          type="number"
          min={1}
          max={65535}
          disabled={props.disabled || props.pending}
          {...form.register("port", { valueAsNumber: true })}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="nvke-spire-timeout">{t("timeout")}</Label>
        <Input
          id="nvke-spire-timeout"
          type="number"
          min={30}
          max={86400}
          disabled={props.disabled || props.pending || props.recovery}
          {...form.register("timeoutSeconds", { valueAsNumber: true })}
        />
      </div>
      {Object.keys(form.formState.errors).length > 0 && (
        <p role="alert" className="text-danger-fg text-sm">
          {t("invalidForm")}
        </p>
      )}
      <Button type="submit" disabled={props.disabled || props.pending}>
        {t(props.pending ? "working" : "reviewRequest")}
      </Button>
    </form>
  );
}
