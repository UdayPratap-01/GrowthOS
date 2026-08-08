import { ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
};

export function Button({ className, variant = "primary", size = "md", ...props }: Props) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-50",
        variant === "primary" && "bg-[var(--accent)] text-[var(--ink)] hover:bg-[var(--accent-strong)]",
        variant === "secondary" && "border border-[var(--line)] bg-[var(--surface)] text-[var(--ink)] hover:bg-[var(--surface-2)]",
        variant === "ghost" && "text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]",
        variant === "danger" && "bg-rose-600 text-white hover:bg-rose-500",
        size === "sm" && "h-8 px-3 text-xs",
        size === "md" && "h-10 px-4 text-sm",
        size === "lg" && "h-12 px-5 text-sm",
        className
      )}
      {...props}
    />
  );
}
