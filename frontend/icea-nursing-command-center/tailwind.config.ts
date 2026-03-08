import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}"
  ],
  theme: {
    extend: {
      // White-label via CSS variables (tokens). Do NOT hardcode brand colors here.
      colors: {
        brand: "hsl(var(--icea-brand) / <alpha-value>)",
        brand2: "hsl(var(--icea-brand2) / <alpha-value>)",
        danger: "hsl(var(--icea-danger) / <alpha-value>)",
        warning: "hsl(var(--icea-warning) / <alpha-value>)",
        ok: "hsl(var(--icea-ok) / <alpha-value>)",
        surface: "hsl(var(--icea-surface) / <alpha-value>)",
        text: "hsl(var(--icea-text) / <alpha-value>)"
      },
      borderRadius: {
        icea: "var(--icea-radius)"
      }
    }
  },
  plugins: []
};

export default config;
