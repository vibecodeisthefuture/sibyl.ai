import { CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Cpu } from "lucide-react";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

export function EngineStatus({ timeRange = "1M" }: { timeRange?: string }) {
  // Dynamically alter the values based on the global Portfolio View window
  const getMultiplier = () => {
    switch(timeRange) {
      case '5D': return 0.95;
      case '1M': return 1.0;
      case '3M': return 1.1;
      case '6M': return 1.25;
      case 'YTD': return 1.15;
      case '1Y': return 1.5;
      case 'ALL': return 2.0;
      default: return 1.0;
    }
  }
  const m = getMultiplier();

  const mockEngines = [
    { 
      name: 'SGE', 
      capital: Math.round(1425 * m), 
      exposure: (12.5 * m).toFixed(1) + '%', 
      confidence: Math.min(99, Math.round(92 * (m > 1 ? 1.02 : 0.98))) + '%', 
      status: 'Active', 
      description: 'The Sentiment Gathering Engine continuously monitors global financial news, social media streams, and macro-economic indicators to establish a real-time market sentiment baseline. It forms the foundational directional bias for the overall portfolio.',
    },
    { 
      name: 'ACE', 
      capital: Math.round(934 * m), 
      exposure: (8.2 * m).toFixed(1) + '%', 
      confidence: Math.min(99, Math.round(88 * (m > 1 ? 1.02 : 0.98))) + '%', 
      status: 'Active', 
      description: 'The Action Control Engine evaluates aggressive opportunistic entries and handles position sizing based on calculated risk-to-reward ratios. It executes systematic trades when conviction crosses the threshold.',
    },
    { 
      name: 'BLITZ', 
      capital: Math.round(512 * m), 
      exposure: (4.5 * m).toFixed(1) + '%', 
      confidence: Math.min(99, Math.round(79 * (m > 1 ? 1.02 : 0.98))) + '%', 
      status: 'Active', 
      description: 'The High-Frequency model captures micro-fluctuations in order flow and executes rapid-fire scalping maneuvers during periods of extreme volatility. It maintains low sustained exposure.',
    }
  ];

  return (
    <>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-lg">Engine Status</CardTitle>
        <Cpu className="size-4 text-muted-foreground" />
      </CardHeader>
      
      <CardContent className="flex flex-col flex-1 pl-6 pr-6 gap-3 mt-2">
        <TooltipProvider delay={150}>
          {mockEngines.map((engine) => (
            <Tooltip key={engine.name}>
              <TooltipTrigger className="flex flex-col justify-center flex-1 h-full gap-4 p-4 rounded-lg border border-border/50 bg-card hover:bg-muted/50 transition-colors cursor-help group text-left w-full">
                <div className="flex justify-between items-center w-full">
                  <span className="font-bold text-sm group-hover:text-primary transition-colors">{engine.name} Engine</span>
                  <span className="text-[10px] uppercase font-bold tracking-wider bg-emerald-500/10 text-emerald-500 px-2 py-0.5 rounded">
                    {engine.status}
                  </span>
                </div>
                
                <div className="flex flex-col gap-3 w-full">
                  <div className="flex justify-between w-full">
                    <div className="flex flex-col">
                      <span className="text-muted-foreground text-[10px] uppercase font-bold tracking-wider mb-0.5">Capital</span>
                      <span className="font-mono font-medium text-sm">${engine.capital.toLocaleString()}</span>
                    </div>
                    <div className="flex flex-col items-end">
                      <span className="text-muted-foreground text-[10px] uppercase font-bold tracking-wider mb-0.5">Exposure</span>
                      <span className="font-mono font-medium text-sm">{engine.exposure}</span>
                    </div>
                  </div>
                  
                  <div className="flex flex-col gap-1.5 w-full mt-1">
                    <div className="flex justify-between items-center w-full">
                      <span className="text-muted-foreground text-[10px] uppercase font-bold tracking-wider">Confidence</span>
                      <span className="font-mono font-bold text-emerald-500 text-xs">{engine.confidence}</span>
                    </div>
                    <div className="h-1.5 w-full bg-secondary rounded-full overflow-hidden">
                      <div className="h-full bg-emerald-500 transition-all duration-500" style={{ width: engine.confidence }} />
                    </div>
                  </div>
                </div>
              </TooltipTrigger>
              <TooltipContent 
                className="w-[280px] p-4 border-none shadow-xl dark:bg-slate-50 dark:text-slate-900 bg-slate-900 text-slate-50 relative z-50"
                side="left"
              >
                <p className="text-xs leading-relaxed font-medium">{engine.description}</p>
              </TooltipContent>
            </Tooltip>
          ))}
        </TooltipProvider>
      </CardContent>
    </>
  );
}
