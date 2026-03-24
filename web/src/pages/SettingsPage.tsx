import { DashboardLayout } from '../components/layout/DashboardLayout'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Checkbox } from "@/components/ui/checkbox"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { useTheme } from "next-themes"

export function SettingsPage() {
  const { theme, setTheme } = useTheme();

  return (
    <DashboardLayout breadcrumbs={[{ label: "Settings" }]}>
      <div className="max-w-3xl mx-auto py-6">
        <h1 className="text-3xl font-bold tracking-tight mb-8">Settings</h1>
        
        <div className="grid gap-6">
          <Card className="shadow-sm">
            <CardHeader>
              <CardTitle>Authentication</CardTitle>
              <CardDescription>Manage your session security and timeout preferences.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="flex items-center space-x-2">
                <Checkbox id="remember" defaultChecked />
                <Label htmlFor="remember" className="text-sm font-medium leading-none cursor-pointer">
                  Remember user logged in?
                </Label>
              </div>
              
              <div className="space-y-3">
                <Label className="text-sm font-medium">Automatically sign out when idle for:</Label>
                <div className="flex items-center space-x-4">
                  <Checkbox id="auto-signout" defaultChecked />
                  <Select defaultValue="15m">
                    <SelectTrigger className="w-[180px]">
                      <SelectValue placeholder="Select timeout" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="5m">5 minutes</SelectItem>
                      <SelectItem value="15m">15 minutes</SelectItem>
                      <SelectItem value="30m">30 minutes</SelectItem>
                      <SelectItem value="1h">1 hour</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="shadow-sm">
            <CardHeader>
              <CardTitle>Appearance</CardTitle>
              <CardDescription>Customize the look and feel of the dashboard.</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                <Label className="text-sm font-medium">Default Theme</Label>
                <RadioGroup value={theme} onValueChange={(t) => setTheme(t)} className="flex flex-col space-y-3 mt-2">
                  <div className="flex items-center space-x-2">
                    <RadioGroupItem value="light" id="theme-light" />
                    <Label htmlFor="theme-light" className="font-normal cursor-pointer">Light Mode</Label>
                  </div>
                  <div className="flex items-center space-x-2">
                    <RadioGroupItem value="dark" id="theme-dark" />
                    <Label htmlFor="theme-dark" className="font-normal cursor-pointer">Dark Mode</Label>
                  </div>
                </RadioGroup>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </DashboardLayout>
  )
}
