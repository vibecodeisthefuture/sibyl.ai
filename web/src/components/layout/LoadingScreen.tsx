import { Progress } from "@/components/ui/progress"
import { useEffect, useState } from "react"
import { motion } from "framer-motion"

export function LoadingScreen({ onComplete }: { onComplete: () => void }) {
  const [progress, setProgress] = useState(0)

  useEffect(() => {
    // Simulate a quick load
    const timer = setTimeout(() => setProgress(66), 400)
    const timer2 = setTimeout(() => setProgress(100), 1000)
    const fadeTimer = setTimeout(() => onComplete(), 1500)
    return () => {
      clearTimeout(timer)
      clearTimeout(timer2)
      clearTimeout(fadeTimer)
    }
  }, [onComplete])

  return (
    <motion.div 
      initial={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.5, ease: "easeInOut" }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-background"
    >
      <div className="w-[200px]">
        <Progress value={progress} className="h-1 shadow-sm" />
      </div>
    </motion.div>
  )
}
