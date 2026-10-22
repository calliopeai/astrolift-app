// "admin.quotas_enabled" -> "Admin · Quotas Enabled"
export function humanizeKey(key: string): string {
  return key
    .split(".")
    .map((seg) =>
      seg
        .split("_")
        .map((w) => (w ? w.charAt(0).toUpperCase() + w.slice(1) : w))
        .join(" ")
    )
    .join(" · ");
}
