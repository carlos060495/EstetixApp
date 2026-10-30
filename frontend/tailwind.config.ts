import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/components/**/*.{ts,tsx}",
    "./src/app/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: "#0A0A0B",
        side: "#141416",
        card: "#1E1E22",
        "card-alt": "#161618",
        border: "#2A2A2E",
        "border-subtle": "#232327",
        gold: "rgb(var(--accent-rgb) / <alpha-value>)",
        "gold-hover": "var(--accent-hover)",
        "gold-tenue": "var(--accent-tenue)",
        text: "#F5F0E8",
        mute: "#9A968C",
        faint: "#6F6B62",
        success: "#4FA86B",
        warning: "#D9A441",
        danger: "#C5524A",
      },
      fontFamily: {
        display: ["var(--font-display)", "serif"],
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      borderRadius: {
        lg: "10px",
        xl: "14px",
      },
    },
  },
  plugins: [],
};
export default config;
