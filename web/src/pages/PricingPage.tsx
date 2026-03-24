import { Check, Activity } from "lucide-react";

export function PricingPage() {
  return (
    <div className="min-h-screen bg-background flex flex-col items-center py-20 px-4">
      <div className="flex flex-col items-center text-center mb-16 max-w-2xl px-4">
        <h1 className="text-xs font-mono text-teal-600 dark:text-teal-400 uppercase tracking-widest mb-4 bg-teal-500/10 px-3 py-1 rounded-md">Pricing Plans</h1>
        <h2 className="text-4xl md:text-5xl font-bold text-foreground tracking-tight mb-5">Choose your predictive tier</h2>
        <p className="text-lg text-muted-foreground">Unlock the full power of the AI prophecy engine. Upgrade to scale your automated capital deployment.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-8 w-full max-w-[1100px] items-end px-4">
        {/* Basic */}
        <div className="bg-card border border-border rounded-2xl p-8 flex flex-col shadow-sm transition-all duration-300 hover:-translate-y-1.5 hover:shadow-lg flex-1 mt-8">
          <h3 className="text-xl font-bold text-foreground mb-1.5">Basic</h3>
          <p className="text-muted-foreground text-sm mb-6 min-h-[40px]">Perfect for individuals getting started with predictive markets.</p>
          <div className="mb-8">
            <span className="text-4xl font-bold text-foreground">$69</span>
            <span className="text-muted-foreground">/mo</span>
          </div>
          <ul className="space-y-4 mb-8 flex-1">
            {["Real-time SGE Sentiment Engine", "Up to $5k deploying capital", "Standard email support", "Daily aggregate reports"].map((feature, i) => (
              <li key={i} className="flex gap-3 items-center text-sm text-foreground">
                <Check className="w-4 h-4 text-emerald-500 shrink-0" /> {feature}
              </li>
            ))}
          </ul>
          <button className="w-full py-2.5 rounded-lg border border-border font-medium text-foreground hover:bg-secondary transition-colors">Start Basic</button>
        </div>

        {/* Pro (Highlighted) */}
        <div className="bg-card border-2 border-foreground rounded-2xl p-8 flex flex-col shadow-md transition-all duration-300 hover:-translate-y-1.5 hover:shadow-xl flex-1 relative transform md:-translate-y-4">
          <div className="absolute -top-3 left-1/2 -translate-x-1/2 bg-foreground text-background text-[10px] font-bold uppercase tracking-wider px-3 py-1 rounded-full">Most Popular</div>
          <h3 className="text-xl font-bold text-foreground mb-1.5 flex items-center gap-2"><Activity className="w-5 h-5"/> Pro</h3>
          <p className="text-muted-foreground text-sm mb-6 min-h-[40px]">Advanced automation and full ACE execution access.</p>
          <div className="mb-8">
            <span className="text-4xl font-bold text-foreground">$189</span>
            <span className="text-muted-foreground">/mo</span>
          </div>
          <ul className="space-y-4 mb-8 flex-1">
            {["Real-time SGE & ACE Engines", "Up to $50k deploying capital", "Automated Execution Agent", "Real-time portfolio charts", "Priority 24/7 support"].map((feature, i) => (
              <li key={i} className="flex gap-3 items-center text-sm text-foreground">
                <Check className="w-4 h-4 text-emerald-500 shrink-0" /> {feature}
              </li>
            ))}
          </ul>
          <button className="w-full py-2.5 rounded-lg bg-foreground hover:bg-foreground/90 text-background font-medium transition-colors shadow-sm">Upgrade to Pro</button>
        </div>

        {/* Max */}
        <div className="bg-card border border-border rounded-2xl p-8 flex flex-col shadow-sm transition-all duration-300 hover:-translate-y-1.5 hover:shadow-lg flex-1 mt-8">
          <h3 className="text-xl font-bold text-foreground mb-1.5">Max</h3>
          <p className="text-muted-foreground text-sm mb-6 min-h-[40px]">Uncapped scale for institutional level homelabs.</p>
          <div className="mb-8">
            <span className="text-4xl font-bold text-foreground">$349</span>
            <span className="text-muted-foreground">/mo</span>
          </div>
          <ul className="space-y-4 mb-8 flex-1">
            {["Everything in Pro", "Unlimited deploying capital", "Custom strategies & backtesting", "Dedicated account manager", "API access for external bots"].map((feature, i) => (
              <li key={i} className="flex gap-3 items-center text-sm text-foreground">
                <Check className="w-4 h-4 text-emerald-500 shrink-0" /> {feature}
              </li>
            ))}
          </ul>
          <button className="w-full py-2.5 rounded-lg border border-border font-medium text-foreground hover:bg-secondary transition-colors">Contact Sales</button>
        </div>
      </div>
    </div>
  );
}
