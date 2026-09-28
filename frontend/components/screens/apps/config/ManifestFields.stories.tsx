import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG } from "./app-config-manifest.fixtures";
import {
  KeyValueEditor,
  ListField,
  NumberField,
  SelectField,
  SpecField,
  TextField,
  ToggleField,
} from "./ManifestFields";

const meta: Meta = {
  title: "Screens/Apps/Config/ManifestFields",
};
export default meta;

type Story = StoryObj;

const noop = () => {};

/** Every field primitive with a value. The fields have no loading state. */
export const Full: Story = {
  render: () => (
    <div className="grid max-w-2xl gap-3 sm:grid-cols-2">
      <TextField label="Name" value="web" mono help="Lowercase, dashes allowed" onChange={noop} />
      <NumberField label="Replicas" value={2} onChange={noop} />
      <ToggleField label="Public" value onChange={noop} />
      <SelectField
        label="Kind"
        value="deployment"
        options={["deployment", "cronjob"]}
        onChange={noop}
      />
      <ListField label="Command" value={["node", "server.js"]} onChange={noop} />
      <SpecField
        spec={{ key: "timeout", label: "Timeout (s)", widget: "number" }}
        bag={{ timeout: 30 }}
        onPatch={noop}
      />
      <div className="sm:col-span-2">
        <KeyValueEditor
          entries={[
            { key: "LOG_LEVEL", value: "info" },
            { key: "DATABASE_URL", value: { from: "db" } },
          ]}
          onChange={noop}
          addLabel="Add variable"
        />
      </div>
    </div>
  ),
};

export const Empty: Story = {
  render: () => (
    <div className="grid max-w-2xl gap-3 sm:grid-cols-2">
      <TextField label="Name" value="" placeholder="web" onChange={noop} />
      <NumberField label="Replicas" value={null} placeholder="1" onChange={noop} />
      <SelectField label="Kind" value="" options={["deployment", "cronjob"]} onChange={noop} />
      <ListField label="Args" value={[]} placeholder="Press Enter to add" onChange={noop} />
      <div className="sm:col-span-2">
        <KeyValueEditor entries={[]} onChange={noop} addLabel="Add variable" />
      </div>
    </div>
  ),
};

/** The error state: inline errors replace the help text. */
export const WithErrors: Story = {
  render: () => (
    <div className="grid max-w-2xl gap-3 sm:grid-cols-2">
      <TextField
        label="Name"
        value=""
        help="hidden by the error"
        error="name is required"
        onChange={noop}
      />
      <NumberField label="Port" value={null} error="port must be an integer" onChange={noop} />
      <SelectField
        label="Kind"
        value=""
        options={["deployment"]}
        error="kind is required"
        onChange={noop}
      />
    </div>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <div className="grid max-w-2xl gap-3 sm:grid-cols-2">
      <TextField label={LONG} value={LONG} mono help={LONG} onChange={noop} />
      <ToggleField label={LONG} value={false} help={LONG} onChange={noop} />
      <div className="sm:col-span-2">
        <KeyValueEditor entries={[{ key: LONG, value: LONG }]} onChange={noop} addLabel={LONG} />
      </div>
    </div>
  ),
};
