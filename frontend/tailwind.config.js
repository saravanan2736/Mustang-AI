/** @type {import('tailwindcss').Config} */
// Colours come from CSS variables in src/styles/globals.css (one dark palette, "Void Space").
// Use the semantic names (bg-canvas, bg-panel, border-line, text-fg, text-muted, bg-accent ...)
// instead of one-off hex values.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: v("canvas"),
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
        menu: "0 2px 8px rgba(0, 0, 0, 0.25)",
      },
    },
  },
  plugins: [],
};
