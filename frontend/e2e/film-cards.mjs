// Title and end cards for the demo film, rendered in the dashboard's own design (pastel sky, glass, Inter).
// Usage (from frontend/): node e2e/film-cards.mjs <out-dir>
import { chromium } from "@playwright/test";
import fs from "fs";
import path from "path";

const OUT = path.resolve(process.argv[2] ?? "../captures");
const ROOT = path.resolve("..");
const FONT = path.resolve("node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2");
const card = (title, sub, foot) => `<!doctype html><html><head><style>
@font-face{font-family:Inter;src:url("file://${FONT}") format("woff2");font-weight:100 900}
html,body{margin:0;width:1440px;height:900px;overflow:hidden;font-family:Inter,sans-serif;background:#fbfaf8;color:#0F1B33}
.sky{position:fixed;inset:-25%;background:
 radial-gradient(38% 42% at 78% 12%,rgba(214,220,250,.85),transparent 70%),
 radial-gradient(34% 40% at 12% 28%,rgba(212,241,228,.8),transparent 70%),
 radial-gradient(30% 36% at 62% 70%,rgba(233,224,249,.75),transparent 70%),
 radial-gradient(28% 34% at 22% 88%,rgba(212,238,244,.7),transparent 70%),
 radial-gradient(22% 26% at 94% 86%,rgba(250,228,232,.55),transparent 70%)}
.wrap{position:relative;height:100%;display:flex;align-items:center;justify-content:center}
.glass{width:960px;padding:64px 72px;border-radius:28px;background:linear-gradient(180deg,rgba(255,255,255,.78),rgba(255,255,255,.55));
 border:1px solid rgba(255,255,255,.85);box-shadow:inset 0 1px 0 #fff,0 30px 80px -30px rgba(15,27,51,.28);backdrop-filter:blur(24px)}
.logo{display:flex;align-items:center;gap:16px;margin-bottom:40px}
.logo b{font-size:26px;font-weight:650;letter-spacing:-.01em}.logo span{display:block;font-size:15px;color:#7A8397;font-weight:500}
h1{margin:0;font-size:58px;line-height:1.08;font-weight:650;letter-spacing:-.025em}
p{margin:22px 0 0;font-size:24px;line-height:1.45;color:#4A5468;font-weight:450}
.foot{margin-top:40px;font-size:15px;letter-spacing:.12em;text-transform:uppercase;color:#7A8397;font-weight:600}
</style></head><body><div class="sky"></div><div class="wrap"><div class="glass">
<div class="logo"><svg viewBox="0 0 32 32" width="56" height="56"><rect width="32" height="32" rx="9" fill="#0F1B33"/>
<path d="M9 22V10l7 7 7-7v12" fill="none" stroke="#E1F5EC" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
<div><b>NanoGate</b><span>Local AI decision control plane</span></div></div>
<h1>${title}</h1><p>${sub}</p><div class="foot">${foot}</div></div></div></body></html>`;

const browser = await chromium.launch({ executablePath: path.join(ROOT, ".runtime/pw-browsers/chromium-1243/chrome-linux-arm64/chrome") });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
for (const [name, t, s, f] of [
  ["intro", "Every AI request takes the cheapest safe route, with proof.",
    "An OpenAI-compatible gateway that decides, on your own hardware, where each request may go, and seals every decision.",
    "HP ZGX Nano · NVIDIA GB10 · real product walkthrough"],
  ["outro", "Safer. Cheaper. Provable.",
    "Privacy rules, verified reuse, local models and tamper-evident receipts, running on a device you own.",
    "NanoGate · github.com/Dev-Faldu/NanoGate"],
]) {
  await page.setContent(card(t, s, f));
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(OUT, `card-${name}.png`) });
  console.log("card", name);
}
await browser.close();
