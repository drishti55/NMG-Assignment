/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#0c0e0d", 900: "#121413", 850: "#171a18", 800: "#1d211f", 700: "#272c29", 600: "#343a37" },
        fg: { DEFAULT: "#e7ebe8", muted: "#a2aaa5", faint: "#6e7671" },
        accent: { DEFAULT: "#3ecf7a", strong: "#22b45f", soft: "#12301f" },
        status: { good: "#0ca30c", warning: "#fab219", serious: "#ec835a", critical: "#d03b3b" },
        series: { 1: "#3987e5", 2: "#d95926", 3: "#199e70" },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
