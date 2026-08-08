import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatCurrency(value: number | string | null | undefined) {
  const n = Number(value ?? 0);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(n);
}

export function formatNumber(value: number | string | null | undefined) {
  return new Intl.NumberFormat("en-US").format(Number(value ?? 0));
}

export function formatPercent(value: number | string | null | undefined) {
  if (value === null || value === undefined) return "—";
  return `${Number(value).toFixed(1)}%`;
}
