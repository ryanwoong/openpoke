import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f2f7f3",
          100: "#e1ece3",
          200: "#c4dacb",
          300: "#a1c1ad",
          400: "#76a187",
          500: "#507e62",
          600: "#244d3d",
          700: "#1b3e30",
          800: "#163226",
          900: "#11281f",
        },
      },
    },
  },
  darkMode: "class",
};

export default config;
