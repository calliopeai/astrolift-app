"use client";

import * as React from "react";
import { toast } from "sonner";

import { downloadCsvContent } from "./exportCsv";

export interface CsvDownload {
  filename: string;
  content: string;
}

/** Download only after the entire matching list succeeds; coalesce repeated clicks. */
export function useCsvExport(load: () => Promise<CsvDownload>) {
  const running = React.useRef(false);
  const [exportingCsv, setExportingCsv] = React.useState(false);
  async function onExportCsv() {
    if (running.current) return;
    running.current = true;
    setExportingCsv(true);
    try {
      const csv = await load();
      downloadCsvContent(csv.filename, csv.content);
    } catch (error) {
      toast.error(error instanceof Error && error.message ? error.message : "The export failed");
    } finally {
      running.current = false;
      setExportingCsv(false);
    }
  }
  return { exportingCsv, onExportCsv };
}
