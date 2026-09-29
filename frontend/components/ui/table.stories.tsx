import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/** The raw table: DataTable's private dependency (no-raw-table), shown for reference. */
const meta: Meta = { title: "Atoms/Table" };
export default meta;

export const Default: StoryObj = {
  render: () => (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Run</TableHead>
          <TableHead>Agent</TableHead>
          <TableHead>Took</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow>
          <TableCell className="font-mono">7f3c…</TableCell>
          <TableCell>support-bot</TableCell>
          <TableCell className="font-mono">0:42</TableCell>
        </TableRow>
      </TableBody>
    </Table>
  ),
};
