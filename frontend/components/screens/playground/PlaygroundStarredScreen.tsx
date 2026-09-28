import { StarIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export type PlaygroundStarredPrompt = {
  title: string;
  prompt: string;
  model: string;
  date: string;
};

/** Starred prompt templates, one card each. */
export function PlaygroundStarredScreen({ items }: { items: PlaygroundStarredPrompt[] }) {
  return (
    <div className="flex flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-xl font-semibold">Starred prompts</h1>
        <p className="text-muted-foreground mt-1 text-sm">Your saved prompt templates.</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {items.map((item) => (
          <Card key={item.title} className="flex flex-col">
            <CardHeader className="flex flex-row items-start justify-between gap-2 pb-2">
              <CardTitle className="text-base">{item.title}</CardTitle>
              <StarIcon className="fill-warning text-warning-fg h-4 w-4 shrink-0" />
            </CardHeader>
            <CardContent className="flex flex-1 flex-col gap-3">
              <p className="text-muted-foreground line-clamp-3 text-sm">{item.prompt}</p>
              <div className="mt-auto flex items-center justify-between">
                <Badge variant="outline">{item.model}</Badge>
                <span className="text-muted-foreground text-xs">{item.date}</span>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
