import { NavMain } from "@/components/nav-main"
import { NavUser } from "@/components/nav-user"
import { Sidebar, SidebarContent, SidebarFooter, SidebarHeader, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarGroup, SidebarGroupContent } from "@/components/ui/sidebar"
import { LayoutDashboardIcon, HistoryIcon, Settings2Icon, Activity, CreditCard, CircleHelpIcon, Moon, Sun } from "lucide-react"
import { useTheme } from "next-themes"
import { Switch } from "@/components/ui/switch"
import { Link, useLocation } from "react-router-dom"

import { Dialog, DialogTrigger, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select"
import { Button } from "@/components/ui/button"

const data = {
  user: {
    name: "Investooor",
    email: "pro@sibyl.ai",
    avatar: "",
    tier: "PRO",
  },
  navMain: [
    {
      title: "Dashboard",
      url: "/dashboard",
      icon: <LayoutDashboardIcon />,
    },
    {
      title: "History",
      url: "/history",
      icon: <HistoryIcon />,
    },
    {
      title: "Upgrade Plan",
      url: "/upgrade",
      icon: <CreditCard />,
    },
  ],
  navSecondary: [
    {
      title: "Get Help",
      url: "/help",
      icon: <CircleHelpIcon />,
    },
    {
      title: "Settings",
      url: "/settings",
      icon: <Settings2Icon />,
    },
  ]
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const { theme, setTheme } = useTheme();
  const location = useLocation();

  return (
    <Sidebar collapsible="icon" className="border-r border-border bg-card" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" render={<Link to="/dashboard" />}>
              <div className="flex aspect-square size-[44px] items-center justify-center rounded-lg bg-teal-600 text-sidebar-primary-foreground shadow-sm shrink-0">
                <Activity className="size-[22px] text-white" />
              </div>
              <div className="grid flex-1 text-left text-sm leading-tight ml-2">
                <span className="truncate font-bold tracking-tight text-foreground text-[22px]">sybil_ai</span>
                <span className="truncate text-xs text-teal-600/80 font-medium mt-0.5">{data.user.tier}</span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      
      <SidebarContent className="flex flex-col h-full bg-card">
        <NavMain items={data.navMain} />
        
        <div className="mt-auto pb-4">
          <SidebarGroup>
            <SidebarGroupContent>
              <SidebarMenu>
                {data.navSecondary.map((item) => {
                  const isActive = location.pathname.startsWith(item.url);
                  return (
                  <SidebarMenuItem key={item.title}>
                    {item.title === "Get Help" ? (
                      <Dialog>
                        <SidebarMenuButton 
                          size="sm" 
                          tooltip={item.title} 
                          className="hover:bg-primary/10 hover:text-primary transition-all duration-200"
                          render={<DialogTrigger />}
                        >
                          {item.icon}
                          <span className="font-medium">{item.title}</span>
                        </SidebarMenuButton>
                        <DialogContent className="sm:max-w-[425px]">
                          <DialogHeader>
                            <DialogTitle>Contact Us</DialogTitle>
                            <DialogDescription>
                              Submit an inquiry below. Our support team will respond via email.
                            </DialogDescription>
                          </DialogHeader>
                          <div className="grid gap-4 py-4">
                            <div className="grid gap-2">
                              <Label htmlFor="name">Full Name</Label>
                              <Input id="name" placeholder="John Doe" />
                            </div>
                            <div className="grid gap-2">
                              <Label htmlFor="email">Account Email</Label>
                              <Input id="email" type="email" placeholder="john@example.com" />
                            </div>
                            <div className="grid gap-2">
                              <Label htmlFor="title">Inquiry Title</Label>
                              <Input id="title" placeholder="Brief summary of issue" />
                            </div>
                            <div className="grid gap-2">
                              <Label htmlFor="type">Inquiry Type</Label>
                              <Select>
                                <SelectTrigger id="type">
                                  <SelectValue placeholder="Select an inquiry type" />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="technical">Technical Support</SelectItem>
                                  <SelectItem value="billing">Billing Issue</SelectItem>
                                  <SelectItem value="feedback">General Feedback</SelectItem>
                                </SelectContent>
                              </Select>
                            </div>
                            <div className="grid gap-2">
                              <Label htmlFor="comments">Comments</Label>
                              <Textarea id="comments" placeholder="Explain the problem you are encountering" className="h-24" />
                            </div>
                          </div>
                          <DialogFooter>
                            <Button type="submit" className="w-full">Submit Inquiry</Button>
                          </DialogFooter>
                        </DialogContent>
                      </Dialog>
                    ) : (
                      <SidebarMenuButton 
                        size="sm" 
                        tooltip={item.title} 
                        isActive={isActive}
                        className="hover:bg-primary/10 hover:text-primary transition-all duration-200"
                        render={<Link to={item.url} />}
                      >
                        {item.icon}
                        <span className="font-medium">{item.title}</span>
                      </SidebarMenuButton>
                    )}
                  </SidebarMenuItem>
                )})}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </div>
      </SidebarContent>
      
      <SidebarFooter className="bg-card pb-4">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton 
              tooltip="Toggle Theme" 
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              className="flex justify-between items-center w-full"
            >
              <div className="flex items-center gap-2">
                {theme === 'dark' ? <Moon className="size-4" /> : <Sun className="size-4" />}
                <span>Theme</span>
              </div>
              <Switch 
                checked={theme === 'dark'} 
                onCheckedChange={(checked) => setTheme(checked ? 'dark' : 'light')} 
                className="group-data-[collapsible=icon]:hidden"
              />
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
        <NavUser user={data.user} />
      </SidebarFooter>
    </Sidebar>
  )
}
