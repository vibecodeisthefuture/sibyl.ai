import { useState, useMemo } from "react";
import { Area, AreaChart, ResponsiveContainer, XAxis, YAxis, Tooltip, CartesianGrid } from "recharts";
import { useTheme } from "next-themes";
import { CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Separator } from "@/components/ui/separator";

function generateMockData() {
  const dailyData = [];
  let currentValue = 10000;
  const now = new Date();
  
  // 3 years of daily data
  for (let i = 1095; i >= 0; i--) {
    const date = new Date(now);
    date.setDate(now.getDate() - i);
    
    // Random walk with slight positive trend
    const change = (Math.random() - 0.48) * 150; 
    currentValue += change;
    if (currentValue < 100) currentValue = 100;
    
    dailyData.push({
      time: date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }),
      value: Math.round(currentValue),
      fullDate: date
    });
  }

  return dailyData;
}

export function PortfolioChart({ timeRange = "1M", setTimeRange }: { timeRange?: string, setTimeRange?: (val: string) => void }) {
  const { theme } = useTheme();
  const isDark = theme === 'dark';
  const [localTimeRange, setLocalTimeRange] = useState(timeRange);
  
  const currentRange = setTimeRange ? timeRange : localTimeRange;
  const updateRange = setTimeRange || setLocalTimeRange;

  const dailyData = useMemo(() => generateMockData(), []);

  const chartData = useMemo(() => {
    switch (currentRange) {
      case "5D": return dailyData.slice(-5);
      case "1M": return dailyData.slice(-30);
      case "3M": return dailyData.slice(-90);
      case "6M": return dailyData.slice(-180);
      case "YTD": {
        const startOfYear = new Date(new Date().getFullYear(), 0, 1);
        const daysSinceStart = Math.floor((new Date().getTime() - startOfYear.getTime()) / (1000 * 60 * 60 * 24));
        return dailyData.slice(-(Math.max(1, daysSinceStart)));
      }
      case "1Y": return dailyData.slice(-365);
      case "ALL": return dailyData;
      default: return dailyData.slice(-30);
    }
  }, [currentRange, dailyData]);

  const isGain = chartData.length > 0 && chartData[chartData.length - 1].value >= chartData[0].value;
  const lineColor = isGain ? '#10b981' : '#ef4444'; // emerald-500 : red-500

  // Mock static values for Open and Day P&L
  const openPnlPct = isGain ? "+12.4%" : "-4.2%";
  const openPnlVal = isGain ? "+$2,450.00" : "-$1,220.50";
  const dayPnlPct = "-1.2%";
  const dayPnlVal = "-$340.50";

  // Formatter for YAxis
  const yAxisFormatter = (val: number) => {
    if (val >= 1000000) return `$${(val / 1000000).toFixed(1).replace(/\.0$/, '')}M`;
    if (val >= 1000) return `$${(val / 1000).toFixed(1).replace(/\.0$/, '')}K`;
    return `$${val}`;
  };

  // Evaluate dynamic X-Axis contextual bounding
  const spansMultipleYears = useMemo(() => {
    if (!chartData || chartData.length === 0) return false;
    const firstYear = chartData[0].fullDate.getFullYear();
    const lastYear = chartData[chartData.length - 1].fullDate.getFullYear();
    return firstYear !== lastYear;
  }, [chartData]);

  const xAxisFormatter = (val: string) => {
    if (!spansMultipleYears) {
      return val.split(',')[0];
    }
    return val;
  };

  return (
    <>
      <CardHeader className="flex gap-4 xl:flex-row xl:items-center xl:justify-between pb-4">
        <div className="flex flex-col gap-2 flex-1">
          <div className="flex flex-col gap-1">
            <CardTitle className="text-lg">Portfolio Overview</CardTitle>
            
            <div className="flex gap-6 mt-1.5">
              <div className="flex flex-col">
                <span className="text-[10px] text-muted-foreground uppercase tracking-wider font-semibold mb-0.5">Open P&L</span>
                <span className={`text-sm ${isGain ? 'text-emerald-500' : 'text-destructive'}`}>
                  {openPnlVal} <span className="text-xs ml-1">({openPnlPct})</span>
                </span>
              </div>
              <div className="flex flex-col">
                <span className="text-[10px] text-muted-foreground uppercase tracking-wider font-semibold mb-0.5">Day P&L</span>
                <span className="text-sm text-destructive">
                  {dayPnlVal} <span className="text-xs ml-1">({dayPnlPct})</span>
                </span>
              </div>
            </div>
          </div>
          <Separator className="mt-2" />
        </div>
        <Tabs value={currentRange} onValueChange={updateRange} className="flex-shrink-0">
          <TabsList className="flex flex-wrap h-auto">
            <TabsTrigger value="5D">5D</TabsTrigger>
            <TabsTrigger value="1M">1M</TabsTrigger>
            <TabsTrigger value="3M">3M</TabsTrigger>
            <TabsTrigger value="6M">6M</TabsTrigger>
            <TabsTrigger value="YTD">YTD</TabsTrigger>
            <TabsTrigger value="1Y">1Y</TabsTrigger>
            <TabsTrigger value="ALL">ALL</TabsTrigger>
          </TabsList>
        </Tabs>
      </CardHeader>
      
      <CardContent className="flex-1 w-full min-h-[300px] pt-4 pl-0 pr-6">
        <ResponsiveContainer width="100%" height={350}>
          <AreaChart data={chartData} margin={{ left: 0, right: 0, top: 10, bottom: 0 }}>
            <defs>
              <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={lineColor} stopOpacity={0.3}/>
                <stop offset="95%" stopColor={lineColor} stopOpacity={0}/>
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" vertical={false} strokeOpacity={0.1} />
            <XAxis
              dataKey="time"
              stroke="#888888"
              fontSize={12}
              tickLine={false}
              axisLine={false}
              tickMargin={10}
              minTickGap={30}
              tickFormatter={xAxisFormatter}
            />
            <YAxis
              stroke="#888888"
              fontSize={12}
              tickLine={false}
              axisLine={false}
              tickFormatter={yAxisFormatter}
              domain={['auto', 'auto']}
              width={65}
            />
            <Tooltip
              formatter={(value: any) => [`$${Number(value).toLocaleString()}`, 'Portfolio Value']}
              labelFormatter={(label) => `${label}`}
              cursor={{ stroke: isDark ? '#2A2A2A' : '#E5E5E5', strokeWidth: 2 }}
              contentStyle={{
                backgroundColor: isDark ? '#FAFAFA' : '#121212',
                borderColor: isDark ? '#E5E5E5' : '#2A2A2A',
                borderRadius: '8px',
                color: isDark ? '#1A1A1A' : '#FAFAFA',
              }}
              itemStyle={{ color: isDark ? '#1A1A1A' : '#FAFAFA', fontWeight: '500' }}
            />
            <Area
              type="monotone"
              dataKey="value"
              stroke={lineColor}
              fillOpacity={1}
              fill="url(#colorValue)"
              strokeWidth={3}
              activeDot={{ r: 6, fill: lineColor, strokeWidth: 0 }}
              animationDuration={750}
            />
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </>
  );
}
