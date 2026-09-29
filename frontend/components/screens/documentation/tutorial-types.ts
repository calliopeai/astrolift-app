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

export const levelVariant = (level: string): "default" | "secondary" | "outline" =>
  level === "Beginner" ? "secondary" : level === "Advanced" ? "default" : "outline";
