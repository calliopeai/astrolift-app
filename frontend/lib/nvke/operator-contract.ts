import { z } from "zod";

const kind = z.enum([
  "string",
  "boolean",
  "strings",
  "item-files",
  "file",
  "directory",
  "output",
  "output-directory",
  "choice",
]);
export const operatorContractSchema = z.object({
  revision: z.string().regex(/^[0-9a-f]{40}$/),
  commands: z
    .array(
      z.object({
        key: z.string().regex(/^[a-z][a-z0-9.-]{0,119}$/),
        positionals: z.array(kind).max(16),
        infrastructureChange: z.boolean(),
        options: z
          .array(
            z
              .object({
                name: z.string().regex(/^[a-z][a-z0-9-]{0,127}$/),
                kind,
                required: z.boolean(),
                choices: z.array(z.string().min(1).max(8192)).max(64),
              })
              .refine((row) =>
                row.kind === "choice" ? row.choices.length > 0 : row.choices.length === 0
              )
          )
          .max(128)
          .refine((rows) => new Set(rows.map((row) => row.name)).size === rows.length),
      })
    )
    .min(1)
    .max(128)
    .refine((rows) => new Set(rows.map((row) => row.key)).size === rows.length),
});
export type NvkeOperatorContract = z.infer<typeof operatorContractSchema>;
export type NvkeOperatorCommand = NvkeOperatorContract["commands"][number];

export function operatorTemplateText(
  command: NvkeOperatorCommand,
  siteId: string,
  revision: string
) {
  const files: Record<string, string> = {};
  const initial = (kind: string, name: string, choices: string[] = []): unknown => {
    if (kind === "file") {
      files[name] = "";
      return `input:${name}`;
    }
    if (kind === "directory") return "source:deploy/nico";
    if (kind === "output" || kind === "output-directory") return `output:${name}`;
    if (kind === "boolean") return false;
    if (kind === "strings") return [""];
    if (kind === "item-files") {
      files[name] = "";
      return [`item=input:${name}`];
    }
    if (kind === "choice") return choices[0];
    return "";
  };
  const positionals = command.positionals.map((kind, i) => initial(kind, `argument-${i + 1}`));
  const options = Object.fromEntries(
    command.options
      .filter((row) => row.required)
      .map((row) => [row.name, initial(row.kind, row.name, row.choices)])
  );
  return JSON.stringify(
    { format: 1, revision, siteId, command: command.key, positionals, options, files },
    null,
    2
  );
}
