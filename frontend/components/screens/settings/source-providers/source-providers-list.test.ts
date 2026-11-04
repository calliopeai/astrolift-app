import { describe, expect, it } from "vitest";

import { connection } from "./settings-source-providers.fixtures";
import { narrowHosts, SOURCE_HOSTS_LIST } from "./source-providers-list";

/**
 * The Hosts Status filter narrows on the status the Status column shows,
 * the server's check (#2601), not the stored isActive switch: an App with no
 * installation on its account is switched on and is not active.
 */
const rows = [
  connection("installed", { accountLogin: "calliopeai" }),
  connection("missing", {
    accountLogin: "ConflictHQ",
    installationId: "",
    status: "INSTALLATION_MISSING",
  }),
  connection("refused", { accountLogin: "acme-legacy", status: "INACTIVE" }),
  connection("unchecked", { accountLogin: "acme-labs", status: "UNVERIFIED" }),
];

const names = (status: string) => narrowHosts(rows, { status }).rows.map((c) => c.name);

describe("Hosts status filter", () => {
  it("offers every status the server reports", () => {
    const status = SOURCE_HOSTS_LIST.fields.find((f) => f.key === "status");
    expect(status?.options?.map((o) => o.value)).toEqual([
      "active",
      "inactive",
      "installation_missing",
      "unverified",
    ]);
  });

  it("narrows on the server's status, not the stored switch", () => {
    expect(names("active")).toEqual(["installed"]);
    expect(names("installation_missing")).toEqual(["missing"]);
    expect(names("inactive")).toEqual(["refused"]);
    expect(names("unverified")).toEqual(["unchecked"]);
  });

  it("reads the stored switch for a row whose query asked for no status", () => {
    const bare = connection("bare", {
      isActive: false,
      status: undefined,
      statusDetail: undefined,
    });
    expect(narrowHosts([bare], { status: "inactive" }).rows).toEqual([bare]);
  });
});
