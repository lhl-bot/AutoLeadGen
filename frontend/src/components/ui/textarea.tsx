import * as React from "react"

import { cn } from "@/lib/utils"

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex field-sizing-content min-h-20 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-base text-slate-950 shadow-xs transition-colors outline-none placeholder:text-slate-400 focus-visible:border-indigo-400 focus-visible:ring-3 focus-visible:ring-indigo-500/15 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:opacity-60 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
