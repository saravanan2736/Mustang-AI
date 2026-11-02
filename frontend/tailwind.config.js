/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        obsidian: {
          950: '#07090E',
          900: '#0B0E17',
          850: '#0F131F',
          800: '#141A29',
          700: '#1E2638',
          600: '#2C374E',
        },
        indigo: {
          electric: '#6366F1',
          glow: '#818CF8',
          deep: '#4F46E5',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
      boxShadow: {
        'glass': '0 8px 32px 0 rgba(0, 0, 0, 0.37)',
        'indigo-glow': '0 0 20px -3px rgba(99, 102, 241, 0.35)',
      },
    },
  },
  plugins: [],
}

