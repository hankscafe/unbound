/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Audible-inspired dark palette
        ink: {
          950: "#0b0d10",
          900: "#111418",
          850: "#161a1f",
          800: "#1c2129",
          700: "#252c36",
          600: "#333c48",
        },
        audible: {
          DEFAULT: "#F8991C",
          400: "#ffb14d",
          500: "#F8991C",
          600: "#e07d00",
        },
        accent: "#F8991C",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "Segoe UI", "Roboto", "sans-serif"],
        display: ["Oswald", "Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
