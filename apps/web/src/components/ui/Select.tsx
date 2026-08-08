import { SelectHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "h-10 w-full rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 text-sm text-[var(--ink)] focus:border-[var(--accent)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]/30",
        className
      )}
      {...props}
    >
      {children}
    </select>
  );
}
