import type { FormSubmission } from "@/graphql/forms/forms.types";

export type SparklinePoint = { day: string; count: number };

/** Bucket the loaded submissions by calendar day in the viewer's time zone. */
export function buildSubmissionsSparkline(
  submissions: Pick<FormSubmission, "submittedAt">[],
  timeZone: string,
  now = new Date()
): SparklinePoint[] {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    calendar: "gregory",
    numberingSystem: "latn",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const calendarDay = (date: Date) => {
    const parts = formatter.formatToParts(date);
    const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)!.value;
    return `${part("year")}-${part("month")}-${part("day")}`;
  };

  // This date represents a calendar label, not an instant in the viewer's zone.
  // UTC arithmetic keeps the 30 labels consecutive across daylight-saving changes.
  const today = new Date(`${calendarDay(now)}T00:00:00.000Z`);
  const counts = new Map<string, number>();
  for (let i = 29; i >= 0; i--) {
    const day = new Date(today);
    day.setUTCDate(today.getUTCDate() - i);
    counts.set(day.toISOString().slice(0, 10), 0);
  }

  for (const submission of submissions) {
    if (!submission.submittedAt) continue;
    const instant = new Date(submission.submittedAt);
    if (!Number.isFinite(instant.getTime())) continue;
    const day = calendarDay(instant);
    if (counts.has(day)) counts.set(day, counts.get(day)! + 1);
  }
  return [...counts.entries()].map(([day, count]) => ({ day, count }));
}
