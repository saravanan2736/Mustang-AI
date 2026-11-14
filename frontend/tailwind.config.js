/** @type {import('tailwindcss').Config} */
// Colours come from CSS variables in src/styles/globals.css (Obsidian Glass palette).
// Use the semantic names (bg-canvas, bg-panel, border-line, text-fg, text-muted, bg-accent ...)
// instead of one-off hex values.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: v("canvas"),
        surface: v("surface"),
        panel: v("panel"),
        raised: v("raised"),
        line: v("line"),
        "line-soft": v("line-soft"),
        fg: v("fg"),
        muted: v("muted"),
        subtle: v("subtle"),
        accent: v("accent"),
        "accent-hover": v("accent-hover"),
        link: v("link"),
        success: v("success"),
        warning: v("warning"),
        danger: v("danger"),
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Cascadia Mono", "Consolas", "monospace"],
      },
      boxShadow: {
        menu: "0 4px 24px rgba(0, 0, 0, 0.4), 0 0 1px rgba(255,255,255,0.06)",
        glow: "0 0 24px rgb(168 85 247 / 0.15)",
        "glow-sm": "0 0 12px rgb(168 85 247 / 0.1)",
      },
      backdropBlur: {
        glass: "12px",
      },
      animation: {
        "fade-in": "fadeIn 0.2s ease-out",
        "slide-up": "slideUp 0.25s ease-out",
      },
      keyframes: {
        fadeIn: {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        slideUp: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};
