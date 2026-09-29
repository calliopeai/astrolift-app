import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";

const meta: Meta = { title: "Atoms/Combobox" };
export default meta;

type Region = { id: string; label: string };
const REGIONS: Region[] = [
  { id: "us-west-2", label: "US West (Oregon)" },
  { id: "us-east-1", label: "US East (N. Virginia)" },
  { id: "eu-west-1", label: "Europe (Ireland)" },
];

function Demo() {
  const [value, setValue] = React.useState("");
  return (
    <div className="w-72">
      <Combobox<Region>
        items={REGIONS}
        itemToStringLabel={(r) => r.id}
        inputValue={value}
        onInputValueChange={(v) => setValue(v ?? "")}
      >
        <ComboboxInput placeholder="us-west-2" aria-label="Region" className="font-mono text-xs" />
        <ComboboxContent>
          <ComboboxEmpty>No matching regions: type one in.</ComboboxEmpty>
          <ComboboxList>
            {(item: Region) => (
              <ComboboxItem key={item.id} value={item}>
                <span className="font-mono text-xs">{item.id}</span>
                <span className="text-muted-foreground ml-auto text-xs">{item.label}</span>
              </ComboboxItem>
            )}
          </ComboboxList>
        </ComboboxContent>
      </Combobox>
    </div>
  );
}

export const Default: StoryObj = { render: () => <Demo /> };
