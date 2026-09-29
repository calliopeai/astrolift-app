import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

export type PlaygroundHistorySession = {
  date: string;
  prompt: string;
  model: string;
  tokens: number;
  status: string;
};

const statusVariant = (status: string) => (status === "completed" ? "default" : "destructive");

/** Past playground sessions, newest first. */
export function PlaygroundHistoryScreen({ sessions }: { sessions: PlaygroundHistorySession[] }) {
  return (
    <div className="flex flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-xl font-semibold">History</h1>
        <p className="text-muted-foreground mt-1 text-sm">Past playground sessions.</p>
      </div>

      <Card>
        <CardContent className="p-0">
          <div className="overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-muted/50 border-b">
                  <th className="px-4 py-3 text-left font-medium">Date</th>
                  <th className="px-4 py-3 text-left font-medium">Prompt</th>
                  <th className="px-4 py-3 text-left font-medium">Model</th>
                  <th className="px-4 py-3 text-right font-medium">Tokens</th>
                  <th className="px-4 py-3 text-left font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s, i) => (
                  <tr key={i} className="hover:bg-muted/30 border-b last:border-0">
                    <td className="text-muted-foreground px-4 py-3 whitespace-nowrap">{s.date}</td>
                    <td className="max-w-xs truncate px-4 py-3">{s.prompt}</td>
                    <td className="px-4 py-3">
                      <Badge variant="outline">{s.model}</Badge>
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">{s.tokens}</td>
                    <td className="px-4 py-3">
                      <Badge variant={statusVariant(s.status)}>{s.status}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
