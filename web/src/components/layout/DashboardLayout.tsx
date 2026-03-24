import { useState, type ReactNode } from 'react';
import { SidebarProvider, SidebarInset, SidebarTrigger } from "@/components/ui/sidebar";
import { AppSidebar } from './AppSidebar';
import { LoadingScreen } from './LoadingScreen';
import { AnimatePresence } from 'framer-motion';
import { Breadcrumb, BreadcrumbItem, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb";
import { Bell, Asterisk } from "lucide-react";
import { Link, useLocation } from "react-router-dom";

export interface BreadcrumbLinkType {
  label: string;
  href?: string;
}

export function DashboardLayout({ children, breadcrumbs = [] }: { children: ReactNode, breadcrumbs?: BreadcrumbLinkType[] }) {
  const location = useLocation();
  const [loading, setLoading] = useState(location.pathname === '/dashboard' || location.pathname === '/');
  
  // Mock notification state
  const [hasUnread, setHasUnread] = useState(true);

  return (
    <>
      <AnimatePresence>
        {loading && <LoadingScreen onComplete={() => setLoading(false)} />}
      </AnimatePresence>
      <SidebarProvider>
        <AppSidebar />
        <SidebarInset className="bg-background transition-colors duration-200">
          <header className="flex shrink-0 items-center justify-between h-14 px-4 bg-background border-b border-border/40 sticky top-0 z-10 w-full">
            <div className="flex items-center gap-2">
              <SidebarTrigger className="-ml-1" />
              <Breadcrumb>
                <BreadcrumbList>
                  <BreadcrumbItem className="hidden md:block">
                    {breadcrumbs.length > 0 ? (
                      <Link to="/dashboard" className="transition-colors hover:text-foreground text-muted-foreground font-medium">Dashboard</Link>
                    ) : (
                      <BreadcrumbPage>Dashboard</BreadcrumbPage>
                    )}
                  </BreadcrumbItem>
                  
                  {breadcrumbs.map((crumb, idx) => (
                    <div key={idx} className="flex items-center gap-2">
                      <BreadcrumbSeparator className="hidden md:block" />
                      <BreadcrumbItem>
                        {crumb.href ? (
                          <Link to={crumb.href} className="transition-colors hover:text-foreground text-muted-foreground font-medium">{crumb.label}</Link>
                        ) : (
                          <BreadcrumbPage>{crumb.label}</BreadcrumbPage>
                        )}
                      </BreadcrumbItem>
                    </div>
                  ))}
                </BreadcrumbList>
              </Breadcrumb>
            </div>
            
            <div className="flex items-center gap-4">
              <button 
                onClick={() => setHasUnread(false)}
                className="relative p-2.5 rounded-md bg-card border border-border/50 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
              >
                <Bell className="size-[20px]" />
                {hasUnread && (
                  <Asterisk className="absolute -top-1 -right-1.5 size-4 text-red-500 [filter:drop-shadow(0_0_4px_rgba(239,68,68,0.8))]" strokeWidth={3} />
                )}
              </button>
            </div>
          </header>
          <main className="flex-1 overflow-y-auto p-4 md:px-8 relative pt-4 w-full">
            <div className="max-w-[1600px] mx-auto">
              {children}
            </div>
          </main>
        </SidebarInset>
      </SidebarProvider>
    </>
  );
}
