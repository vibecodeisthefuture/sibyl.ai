import { Terminal } from "lucide-react";
import { CardHeader, CardTitle, CardContent } from "@/components/ui/card";

const mockLogs = [
  { id: 1, type: "WHALE", time: "14:23", message: "Large volume detected on ETH options. Assessing context." },
  { id: 2, type: "SENTIMENT", time: "14:15", message: "X.com sentiment for \"Polymarket\" trending positively (+14%)." },
  { id: 3, type: "TRADE", time: "13:45", message: "SGE Engine opened LONG position on Election Market." },
  { id: 4, type: "SYSTEM", time: "12:00", message: "Routine cycle completed. Capital re-allocated: $450 to ACE." },
];

export function ActivityLogs() {
  return (
    <>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-lg">Terminal Logs</CardTitle>
        <Terminal className="size-4 text-muted-foreground" />
      </CardHeader>
      
      <CardContent className="flex-1 flex flex-col p-6 pt-0">
        <div className="flex-1 bg-secondary/30 rounded-xl border border-border p-4 overflow-y-auto flex flex-col gap-3 font-mono text-sm max-h-[250px]">
          {mockLogs.map(log => (
            <div key={log.id} className="flex gap-3 items-start border-b border-border/40 pb-2 last:border-0 last:pb-0">
              <span className="text-muted-foreground shrink-0 text-xs mt-0.5">[{log.time}]</span>
              <span className={`shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded uppercase mt-0.5 ${
                log.type === 'WHALE' ? 'bg-purple-500/10 text-purple-600 dark:text-purple-400' :
                log.type === 'SENTIMENT' ? 'bg-blue-500/10 text-blue-600 dark:text-blue-400' :
                log.type === 'TRADE' ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400' : 'bg-gray-500/10 text-gray-600 dark:text-gray-400'
              }`}>
                {log.type}
              </span>
              <span className="text-foreground text-xs leading-relaxed break-words">
                {log.message}
              </span>
            </div>
          ))}
          {/* Blinking cursor */}
          <div className="flex gap-3 animate-pulse pt-2">
            <span className="text-muted-foreground w-2 h-4 bg-muted-foreground/50 inline-block"></span>
          </div>
        </div>
      </CardContent>
    </>
  );
}
