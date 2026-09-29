import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { NewRowsPill } from "./NewRowsPill";

/** "3 new ↑" on live lists (spec 44 §5.1). */
const meta: Meta = { title: "List/NewRowsPill", parameters: { layout: "padded" } };
export default meta;

export const Three: StoryObj = { render: () => <NewRowsPill count={3} onReveal={() => {}} /> };
export const Many: StoryObj = { render: () => <NewRowsPill count={250} onReveal={() => {}} /> };

/** At zero it renders nothing visible (the live region stays for the next count). */
export const None: StoryObj = { render: () => <NewRowsPill count={0} onReveal={() => {}} /> };

export const Interactive: StoryObj = {
  render: () => {
    function Demo() {
      const [count, setCount] = React.useState(3);
      return <NewRowsPill count={count} onReveal={() => setCount(0)} />;
    }
    return <Demo />;
  },
};
