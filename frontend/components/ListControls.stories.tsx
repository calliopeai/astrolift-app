import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ListControls } from "@/components/ListControls";
import { useListControls } from "@/hooks/use-list-controls";

/** Client-side search, sort and paging over an in-memory list. */
const meta: Meta = { title: "Patterns/ListControls" };
export default meta;

const APPS = Array.from({ length: 60 }, (_, i) => ({
  name: `app-${String(i + 1).padStart(2, "0")}`,
}));

function Demo() {
  const controls = useListControls({ data: APPS, searchFn: (a) => a.name });
  return (
    <div className="flex max-w-xl flex-col gap-3">
      <ListControls controls={controls} searchPlaceholder="Search apps…" />
      <ul className="text-sm">
        {controls.rows.map((a) => (
          <li key={a.name} className="font-mono">
            {a.name}
          </li>
        ))}
      </ul>
    </div>
  );
}

export const Default: StoryObj = { render: () => <Demo /> };
