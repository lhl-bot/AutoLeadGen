import * as React from "react"
import { Input as InputPrimitive } from "@base-ui/react/input"

import { cn } from "@/lib/utils"

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <InputPrimitive
      type={type}
      data-slot="input"
      className={cn(
        "h-9 w-full min-w-0 rounded-lg border border-slate-200 bg-white px-3 py-1 text-base text-slate-950 shadow-xs transition-colors outline-none file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-slate-950 placeholder:text-slate-400 focus-visible:border-indigo-400 focus-visible:ring-3 focus-visible:ring-indigo-500/15 disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-slate-100 disabled:opacity-60 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm",
        className
      )}
      {...props}
    />
  )
}

export { Input }
