/** NanoGate design tokens: light, editorial, calm. 8px spacing grid, 16px card radius. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Inter Variable"', "system-ui", "-apple-system", '"Segoe UI"', "sans-serif"],
        mono: ['"JetBrains Mono Variable"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
      colors: {
        ivory: "#FAF8F4",
        paper: "#FFFFFF",
        line: "#E4E6EB",
        hair: "#EEF0F3",
        ink: { DEFAULT: "#0F1B33", 2: "#4A5468", 3: "#7A8397" },
        mint: { soft: "#E1F5EC", DEFAULT: "#0E7A55" },
        cyan: { soft: "#E0F3F7", DEFAULT: "#0B6E85" },
        peri: { soft: "#E6E9FB", DEFAULT: "#3D4FB8" },
        lilac: { soft: "#EFE8FA", DEFAULT: "#6B45B0" },
        blush: { soft: "#FBEAEC", DEFAULT: "#A8374A" },
        warn: { soft: "#FDF2DE", DEFAULT: "#8F5600" },
        danger: { soft: "#FBE7E6", DEFAULT: "#B42F2F" },
      },
      borderRadius: { card: "16px" },
      boxShadow: {
        soft: "0 1px 2px rgba(15,27,51,0.04), 0 8px 24px -12px rgba(15,27,51,0.10)",
        lift: "0 2px 4px rgba(15,27,51,0.05), 0 16px 40px -16px rgba(15,27,51,0.18)",
      },
      transitionDuration: { 180: "180ms" },
      keyframes: {
        fadein: { from: { opacity: 0, transform: "translateY(4px)" }, to: { opacity: 1, transform: "none" } },
        slidein: { from: { transform: "translateX(24px)", opacity: 0 }, to: { transform: "none", opacity: 1 } },
        seal: { "0%": { boxShadow: "0 0 0 0 rgba(14,122,85,0.35)" }, "100%": { boxShadow: "0 0 0 14px rgba(14,122,85,0)" } },
      },
      animation: {
        fadein: "fadein 200ms ease-out both",
        slidein: "slidein 220ms ease-out both",
        seal: "seal 900ms ease-out 1",
      },
    },
  },
  plugins: [],
};
