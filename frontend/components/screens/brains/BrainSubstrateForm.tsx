"use client";

import { CheckCircle2Icon, LockIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import { WizardField } from "./new/WizardField";
import {
  type Axis,
  AXES,
  AXIS_ORDER,
  type CellMatrix,
  cellStatus,
  MAX_REPLICAS,
  optionStatus,
  PERSISTENCE,
  type Persistence,
  requiredPersistence,
  substrateProblems,
  type SubstrateDraft,
} from "./substrate-cells";

export interface SubstrateServerError {
  /** The field the server named (`authority`, `persistence`, `replicas`…), if any. */
  field: string | null;
  message: string;
}

export interface BrainSubstrateFormProps {
  draft: SubstrateDraft;
  onChange: (next: SubstrateDraft) => void;
  /** Mimwell's matrix (`astroliftBrainCells`); null until it arrives, when nothing is refused here. */
  matrix: CellMatrix | null;
  /** Create may name an image; graduation keeps the one the brain runs. */
  mode: "create" | "graduate";
  /** Persistence that must stay attached: what the brain already holds data on. */
  locked?: readonly Persistence[];
  serverError?: SubstrateServerError | null;
  /** Keeps ids unique when the form shows twice on a page. */
  idPrefix?: string;
}

/**
 * Mimwell's substrate as a form (#2424, #2427): the cell's four placements,
 * each option that would make an unsupported cell disabled with Mimwell's
 * reason as the server gives it (#2444); the persistence the host attaches, with what the cell needs
 * checked and held; replicas, and the image and port. Shared by the create
 * wizard and graduation. Pure.
 */
export function BrainSubstrateForm({
  draft,
  onChange,
  matrix,
  mode,
  locked = [],
  serverError,
  idPrefix = "substrate",
}: BrainSubstrateFormProps) {
  const t = useTranslations("brains.substrate");
  const required = requiredPersistence(draft.cell);
  const verdict = cellStatus(draft.cell, matrix);
  const problems = substrateProblems(draft, matrix);
  const named = serverError?.field ?? null;
  const fieldError = (field: string) => (named === field ? serverError!.message : null);
  // A refusal about one of the lower fields sits on that field; one about
  // the cell, or about nothing in particular, leads the form.
  const below = ["persistence", "replicas", "port", ...(mode === "create" ? ["image"] : [])];
  const general = serverError && !below.includes(named ?? "") ? serverError.message : null;
  const setCell = <A extends Axis>(axis: A, value: string) =>
    onChange({ ...draft, cell: { ...draft.cell, [axis]: value } });
  const togglePersistence = (p: Persistence, on: boolean) =>
    onChange({
      ...draft,
      persistence: on
        ? [...draft.persistence.filter((x) => x !== p), p]
        : draft.persistence.filter((x) => x !== p),
    });
  return (
    <div className="flex min-w-0 flex-col gap-6">
      {general && (
        <p
          role="alert"
          className="border-danger-border bg-danger/5 text-danger-fg rounded-md border px-3 py-2 font-mono text-xs [overflow-wrap:anywhere]"
        >
          {general}
        </p>
      )}

      <fieldset className="min-w-0 space-y-3">
        <legend className="text-sm font-medium">{t("cell")}</legend>
        <p className="text-muted-foreground text-xs">{t("cellHelp")}</p>
        <div className="grid min-w-0 gap-4 sm:grid-cols-2">
          {AXIS_ORDER.map((axis) => (
            <div key={axis} className="min-w-0 space-y-2">
              <Label htmlFor={`${idPrefix}-${axis}`}>{t(`axes.${axis}`)}</Label>
              <Select value={draft.cell[axis]} onValueChange={(v) => setCell(axis, v)}>
                <SelectTrigger id={`${idPrefix}-${axis}`} className="w-full font-mono">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(AXES[axis] as readonly string[]).map((value) => {
                    const option = optionStatus(draft.cell, axis, value as never, matrix);
                    const refused = option?.supported === false ? option : null;
                    return (
                      <SelectItem key={value} value={value} disabled={Boolean(refused)}>
                        <span className="flex min-w-0 flex-col">
                          <span className="font-mono">{value}</span>
                          {refused?.reason && (
                            <span className="text-muted-foreground text-xs">{refused.reason}</span>
                          )}
                        </span>
                      </SelectItem>
                    );
                  })}
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-xs">{t(`axisHelp.${axis}`)}</p>
            </div>
          ))}
        </div>
        <p
          className={cn(
            "flex min-w-0 items-start gap-2 rounded-md border px-3 py-2 text-xs",
            !verdict
              ? "text-muted-foreground"
              : verdict.supported
                ? "border-success-border bg-success/10 text-success-fg"
                : "border-danger-border bg-danger/5 text-danger-fg"
          )}
        >
          {verdict?.supported && (
            <CheckCircle2Icon className="mt-px size-3.5 shrink-0" aria-hidden />
          )}
          <span className="min-w-0 [overflow-wrap:anywhere]">
            {verdict && (verdict.supported ? t("supported") : verdict.reason)}{" "}
            <span className="font-mono">
              {AXIS_ORDER.map((axis) => `${axis}=${draft.cell[axis]}`).join(", ")}
            </span>
          </span>
        </p>
      </fieldset>

      <fieldset className="min-w-0 space-y-3">
        <legend className="text-sm font-medium">{t("persistence")}</legend>
        <p className="text-muted-foreground text-xs">{t("persistenceHelp")}</p>
        <ul className="divide-border min-w-0 divide-y rounded-md border">
          {PERSISTENCE.map((p) => {
            const neededBy = required.get(p);
            const held = locked.includes(p);
            const checked = Boolean(neededBy) || held || draft.persistence.includes(p);
            const id = `${idPrefix}-persistence-${p}`;
            return (
              <li key={p} className="flex min-w-0 items-start gap-3 px-3 py-2.5">
                <Checkbox
                  id={id}
                  checked={checked}
                  disabled={Boolean(neededBy) || held}
                  onCheckedChange={(on) => togglePersistence(p, on === true)}
                  aria-describedby={`${id}-note`}
                  className="mt-0.5"
                />
                <div className="min-w-0 flex-1">
                  <Label htmlFor={id} className="font-mono">
                    {p}
                  </Label>
                  <p id={`${id}-note`} className="text-muted-foreground text-xs">
                    {t(`persistenceKinds.${p}`)}
                    {p === "postgres" && draft.cell.graph === "age" && ` ${t("withAge")}`}
                  </p>
                  {(neededBy || held) && (
                    <p className="text-muted-foreground mt-1 inline-flex items-center gap-1 text-xs">
                      <LockIcon className="size-3" aria-hidden />
                      {held
                        ? t("heldData")
                        : t("neededBy", { placement: `${neededBy}=${draft.cell[neededBy!]}` })}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
        {fieldError("persistence") && (
          <p role="alert" className="text-destructive text-xs">
            {fieldError("persistence")}
          </p>
        )}
      </fieldset>

      <div className="grid min-w-0 gap-4 sm:grid-cols-[8rem_1fr_8rem]">
        <WizardField
          id={`${idPrefix}-replicas`}
          label={t("replicas")}
          help={
            draft.persistence.includes("sqlite-volume") || required.has("sqlite-volume")
              ? t("replicasSqlite")
              : t("replicasHelp", { max: MAX_REPLICAS })
          }
          problem={problems.includes("replicas") ? t("replicasRange", { max: MAX_REPLICAS }) : null}
          serverError={fieldError("replicas")}
        >
          <Input
            id={`${idPrefix}-replicas`}
            inputMode="numeric"
            className="font-mono"
            value={Number.isNaN(draft.replicas) ? "" : String(draft.replicas)}
            onChange={(e) => onChange({ ...draft, replicas: Number(e.target.value) })}
            aria-invalid={problems.includes("replicas") || undefined}
            aria-describedby={`${idPrefix}-replicas-note`}
          />
        </WizardField>
        {mode === "create" ? (
          <WizardField
            id={`${idPrefix}-image`}
            label={t("image")}
            help={t("imageHelp")}
            problem={problems.includes("image") ? t("imageFormat") : null}
            serverError={fieldError("image")}
          >
            <Input
              id={`${idPrefix}-image`}
              className="font-mono"
              placeholder="ghcr.io/acme/platform-brain:1.0"
              value={draft.image}
              onChange={(e) => onChange({ ...draft, image: e.target.value })}
              aria-invalid={problems.includes("image") || undefined}
              aria-describedby={`${idPrefix}-image-note`}
              autoComplete="off"
              spellCheck={false}
            />
          </WizardField>
        ) : (
          <p className="text-muted-foreground self-end pb-2 text-xs">{t("imageKept")}</p>
        )}
        <WizardField
          id={`${idPrefix}-port`}
          label={t("port")}
          problem={problems.includes("port") ? t("portRange") : null}
          serverError={fieldError("port")}
        >
          <Input
            id={`${idPrefix}-port`}
            inputMode="numeric"
            className="font-mono"
            value={Number.isNaN(draft.port) ? "" : String(draft.port)}
            onChange={(e) => onChange({ ...draft, port: Number(e.target.value) })}
            aria-invalid={problems.includes("port") || undefined}
            aria-describedby={`${idPrefix}-port-note`}
          />
        </WizardField>
      </div>
    </div>
  );
}
