/** Shape of a tutorial as the documentation screens render it. */
export type Tutorial = {
  slug: string;
  title: string;
  description: string;
  level: string;
  duration: string;
  steps: {
    title: string;
    body: string;
    code?: string;
  }[];
};

/** Message key under `docsTutorials.levels` for a known level; unknown levels show as written. */
export const levelKey = (level: string): "beginner" | "intermediate" | "advanced" | null =>
  level === "Beginner"
    ? "beginner"
    : level === "Intermediate"
      ? "intermediate"
      : level === "Advanced"
        ? "advanced"
        : null;

export const levelVariant = (level: string): "default" | "secondary" | "outline" =>
  level === "Beginner" ? "secondary" : level === "Advanced" ? "default" : "outline";
