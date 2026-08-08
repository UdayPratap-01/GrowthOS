import type { Config } from "tailwindcss";

export default {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-sora)", "sans-serif"],
        display: ["var(--font-sora)", "sans-serif"],
      },
      colors: {
        background: "var(--canvas)",
        foreground: "var(--ink)",
      },
      keyframes: {
        rise: {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        pulseSoft: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.72" },
        },
      },
      animation: {
        rise: "rise 0.45s ease-out both",
        pulseSoft: "pulseSoft 2.4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
