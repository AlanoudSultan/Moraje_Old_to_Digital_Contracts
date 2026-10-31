/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Mapped onto the design tokens in index.css.
      colors: {
        primary: { DEFAULT: "var(--color-accent)", hover: "var(--color-accent-600)", soft: "var(--color-accent-100)" },
        accent2: { DEFAULT: "var(--color-accent-2-600)", soft: "var(--color-accent-2-100)", ink: "var(--color-accent-2-800)" },
        bg: "var(--color-bg)",
        surface: "var(--color-surface)",
        line: "var(--color-divider)",
        ink: { DEFAULT: "var(--color-text)", muted: "var(--color-neutral-700)", soft: "var(--color-neutral-800)" },
        success: "var(--color-accent-700)",
        warning: "var(--color-accent-2-700)",
        danger: "var(--color-danger)",
        info: "var(--color-accent-700)",
      },
      fontFamily: {
        sans: ["Barlow", '"IBM Plex Sans Arabic"', "system-ui", "sans-serif"],
        heading: ['"Barlow Condensed"', '"IBM Plex Sans Arabic"', "system-ui", "sans-serif"],
        arabic: ['"IBM Plex Sans Arabic"', "sans-serif"],
      },
      borderRadius: { card: "0", btn: "8px" },
    },
  },
  plugins: [],
};
