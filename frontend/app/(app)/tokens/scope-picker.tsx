"use client";

import { ScopePicker as ScopePickerView } from "@/components/screens/tokens/ScopePicker";
import { useScopeCatalog } from "@/components/screens/tokens/use-scope-catalog";

/** Pick a token's scopes from the server's catalog (#2120). */
export function ScopePicker({
  value,
  onChange,
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return <ScopePickerView {...useScopeCatalog()} value={value} onChange={onChange} />;
}
