import { useState, useEffect } from 'react'
import { DashboardLayout } from '../components/layout/DashboardLayout'
import { DashboardKPIs } from '../components/dashboard/DashboardKPIs'
import { PortfolioChart } from '../components/dashboard/PortfolioChart'
import { EngineStatus } from '../components/dashboard/EngineStatus'
import { ActivityLogs } from '../components/dashboard/ActivityLogs'
import { ActiveBets } from '../components/dashboard/ActiveBets'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

export function DashboardPage() {
  const [dataLoaded, setDataLoaded] = useState(false)
  const [timeRange, setTimeRange] = useState("1M")

  // Simulate data fetching delay for skeletons
  useEffect(() => {
    const timer = setTimeout(() => setDataLoaded(true), 2500)
    return () => clearTimeout(timer)
  }, [])

  return (
    <DashboardLayout>
      {dataLoaded ? <DashboardKPIs /> : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-6 w-full">
          {[1,2,3,4].map(i => <Skeleton key={i} className="h-[120px] rounded-xl" />)}
        </div>
      )}
      
      <div className="grid grid-cols-12 gap-6 w-full h-full pb-8">
        <Card className="col-span-12 xl:col-span-8 min-h-[400px] shadow-sm flex flex-col">
          {dataLoaded ? <PortfolioChart timeRange={timeRange} setTimeRange={setTimeRange} /> : <Skeleton className="w-full h-full min-h-[400px] rounded-xl" />}
        </Card>
        
        <Card className="col-span-12 xl:col-span-4 shadow-sm flex flex-col">
          {dataLoaded ? <EngineStatus timeRange={timeRange} /> : <Skeleton className="w-full h-full min-h-[400px] rounded-xl" />}
        </Card>
        
        <Card className="col-span-12 lg:col-span-6 min-h-[350px] shadow-sm flex flex-col">
          {dataLoaded ? <ActivityLogs /> : <Skeleton className="w-full h-full min-h-[350px] rounded-xl" />}
        </Card>

        <Card className="col-span-12 lg:col-span-6 min-h-[350px] shadow-sm flex flex-col overflow-hidden">
          {dataLoaded ? <ActiveBets /> : <Skeleton className="w-full h-full min-h-[350px] rounded-xl" />}
        </Card>
      </div>
    </DashboardLayout>
  )
}
