// Full product walkthrough against the running gateway: real clicks, real data, real generations.
// Produces captures/: numbered screenshots, page HTML, design tokens (colors/fonts), a timestamped action log
// and a video. API keys shown on screen are blurred before any capture; demo keys are revoked at the end.
// Usage (from frontend/): node e2e/capture-demo.mjs [base-url] [out-dir]
import { chromium } from "@playwright/test";
import fs from "fs";
import path from "path";
import { execFileSync } from "child_process";

const BASE = process.argv[2] ?? "http://127.0.0.1:8080";
const OUT = path.resolve(process.argv[3] ?? "../captures");
const ROOT = path.resolve("..");
const CHROME = path.join(ROOT, ".runtime/pw-browsers/chromium-1243/chrome-linux-arm64/chrome");
const ADMIN_KEY = JSON.parse(fs.readFileSync(path.join(ROOT, "var/dev_keys.json"), "utf8")).keys.admin.key;
const GEN = 240_000;   // real generations on the device can take a while (14B escalations)

fs.rmSync(OUT, { recursive: true, force: true });
for (const d of ["screens", "html", "video", "frames"]) fs.mkdirSync(path.join(OUT, d), { recursive: true });

const t0 = Date.now();
const log = [];
let n = 0;
const say = (action, detail = "") => {
  const e = { t: +((Date.now() - t0) / 1000).toFixed(2), action, detail };
  log.push(e);
  console.log(`${String(e.t).padStart(7)}s  ${action}${detail ? "  " + detail : ""}`);
};

// visible cursor + click ripple (headless recordings have no pointer); blur anything that looks like an API key
const INIT = () => {
  const css = `
    #__cur{position:fixed;z-index:2147483647;width:22px;height:22px;margin:-3px 0 0 -3px;pointer-events:none;
      background:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='22' height='22'><path d='M3 2l15 8-6.5 1.6L8.6 18z' fill='%230f1b33' stroke='white' stroke-width='1.4'/></svg>") no-repeat;
      transition:left .12s ease-out, top .12s ease-out}
    .__rip{position:fixed;z-index:2147483646;width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;pointer-events:none;
      border:2px solid rgba(61,79,184,.8);animation:__r .5s ease-out forwards}
    @keyframes __r{from{transform:scale(.3);opacity:1}to{transform:scale(1.4);opacity:0}}
    .__secret{filter:blur(7px)!important}`;
  const add = () => {
    if (document.getElementById("__cur")) return;
    const s = document.createElement("style"); s.textContent = css; document.head.appendChild(s);
    const c = document.createElement("div"); c.id = "__cur"; c.style.left = "-40px"; c.style.top = "-40px";
    document.body.appendChild(c);
    addEventListener("mousemove", (e) => { c.style.left = e.clientX + "px"; c.style.top = e.clientY + "px"; }, true);
    addEventListener("mousedown", (e) => {
      const r = document.createElement("div"); r.className = "__rip"; r.style.left = e.clientX + "px"; r.style.top = e.clientY + "px";
      document.body.appendChild(r); setTimeout(() => r.remove(), 600);
    }, true);
    const blur = () => document.querySelectorAll("body *:not(script):not(style)").forEach((el) => {
      if (el.children.length === 0 && /ng_live_[a-f0-9]{8}_/.test(el.textContent || "")) el.classList.add("__secret");
    });
    new MutationObserver(blur).observe(document.body, { subtree: true, childList: true, characterData: true });
  };
  document.readyState === "loading" ? addEventListener("DOMContentLoaded", add) : add();
};

const browser = await chromium.launch({ executablePath: CHROME });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });

// video: Chrome's own screencast (frames with real timestamps), assembled with ffmpeg at the end
const frames = [];
let rec = null;
async function record(p) {
  if (rec) await rec.send("Page.stopScreencast").catch(() => {});
  const s = await ctx.newCDPSession(p);
  s.on("Page.screencastFrame", (f) => {
    const file = `f${String(frames.length).padStart(6, "0")}.jpg`;
    fs.writeFileSync(path.join(OUT, "frames", file), Buffer.from(f.data, "base64"));
    frames.push({ file, ts: f.metadata.timestamp });
    s.send("Page.screencastFrameAck", { sessionId: f.sessionId }).catch(() => {});
  });
  await s.send("Page.startScreencast", { format: "jpeg", quality: 82, maxWidth: 1440, maxHeight: 900, everyNthFrame: 1 });
  rec = s;
}
await ctx.addInitScript(INIT);
const page = await ctx.newPage();
await record(page);
page.on("dialog", (d) => d.accept());
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

const pause = (ms = 700) => page.waitForTimeout(ms);
async function shot(name, p = page) {
  n += 1;
  const f = `${String(n).padStart(2, "0")}-${name}.png`;
  await p.screenshot({ path: path.join(OUT, "screens", f) });
  say("screenshot", f);
}
async function html(name, p = page) {
  fs.writeFileSync(path.join(OUT, "html", `${name}.html`), await p.content());
}
async function click(loc, label, p = page) {
  await loc.scrollIntoViewIfNeeded();
  const b = await loc.boundingBox();
  if (b) { await p.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 18 }); await p.waitForTimeout(250); }
  await loc.click();
  say("click", label);
}
async function type(loc, text, label, p = page) {
  await click(loc, label, p);
  await loc.fill("");
  await loc.pressSequentially(text, { delay: 18 });
  say("type", `${label}: ${text.length > 70 ? text.slice(0, 70) + "…" : text}`);
}
async function hover(loc, label, p = page) {
  await loc.scrollIntoViewIfNeeded();
  const b = await loc.boundingBox();
  if (b) await p.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 18 });
  say("hover", label);
  await p.waitForTimeout(900);
}
async function step(name, fn) {
  try { await fn(); } catch (e) { say("⚠ step failed", `${name}: ${String(e).split("\n")[0]}`); await shot(`failed-${name}`).catch(() => {}); }
}
async function visit(route, name) {
  await page.goto(BASE + route);
  await page.waitForLoadState("networkidle").catch(() => {});
  await pause(1200);
  say("open page", route);
  await shot(name);
  await html(name);
}

const cleanup = { keys: [] };
const api = (method, url, body) => page.evaluate(async ([m, u, b]) => {
  const r = await fetch(u, { method: m, headers: { "Content-Type": "application/json",
    Authorization: `Bearer ${sessionStorage.getItem("nanogate.session")}` }, body: b ? JSON.stringify(b) : undefined });
  return r.json();
}, [method, url, body]);

// ---------------------------------------------------------------------------------------------------------
say("start", BASE);
await page.goto(BASE + "/");
await pause(900);
await shot("login");
await step("login", async () => {
  await type(page.locator("#apikey"), ADMIN_KEY.slice(0, 12), "admin API key (typed partially on camera)");
  await page.locator("#apikey").fill(ADMIN_KEY);           // the rest is filled off-camera (password field anyway)
  await click(page.getByRole("button", { name: "Continue" }), "Continue");
  await page.locator(".try-fab").waitFor({ timeout: 30_000 });
  await pause(1500);
});
await shot("overview");
await html("overview");

await step("kpi-hover", async () => {
  await hover(page.locator(".tile").nth(1), "KPI tile: Local coverage");
  await shot("hover-kpi");
  await hover(page.locator("ol .hint").nth(6), "Decision step: Router");
  await shot("hover-decision-step");
});

// Try it: four real requests through the pipeline
const tryIt = async (preset, label) => {
  const before = (await page.getByTestId("composer-result").count()) ? await page.getByTestId("composer-result").textContent() : null;
  await click(page.getByRole("button", { name: preset }), `preset: ${preset}`);
  await pause(400);
  await click(page.getByTestId("send-request"), "Send real request");
  await page.waitForFunction((b) => {
    const el = document.querySelector('[data-testid="composer-result"]');
    return el && el.textContent !== b && !document.querySelector('[data-testid="send-request"]')?.hasAttribute("disabled");
  }, before, { timeout: GEN });
  await pause(900);
  const reason = (await page.getByTestId("composer-result").textContent())?.slice(0, 80);
  say("result", `${label}: ${reason}`);
  await shot(`tryit-${label}`);
};
await step("try-it", async () => {
  await click(page.locator(".try-fab"), "Try it");
  await pause(600);
  await shot("tryit-open");
  await tryIt("Local intelligence", "local");
  await tryIt("Secret leak attempt", "secret-blocked");
  await tryIt("Privacy (synthetic PII)", "privacy-hr");
  await tryIt("Security (CISA KEV)", "kev-rag");
  await page.keyboard.press("Escape");
  await pause(600);
});

await step("receipt", async () => {
  await click(page.getByRole("button", { name: "Open receipt" }), "Open receipt");
  await pause(1200);
  await hover(page.locator("[role=dialog][aria-modal=true] ol .hint").nth(4), "receipt step: Cache");
  await shot("receipt-drawer");
  await click(page.getByRole("link", { name: /Full receipt/ }), "Full receipt");
  await pause(1500);
  await click(page.getByRole("button", { name: /Verify integrity/ }), "Verify integrity");
  await pause(1500);
  await shot("receipt-verified");
  const tamper = page.getByRole("button", { name: /tamper/i });
  if (await tamper.count()) { await click(tamper.first(), "Run tamper test"); await pause(1500); await shot("receipt-tamper-detected"); }
  await html("receipt");
});

await visit("/requests", "requests");

// Knowledge: create a source, upload a synthetic runbook
let sourceName = "IT runbooks (synthetic demo)";
await step("knowledge", async () => {
  for (const s of (await api("GET", "/api/knowledge")).sources.filter((x) => x.name === sourceName))
    await api("DELETE", `/api/knowledge/${s.source_id}`);
  await visit("/knowledge", "knowledge");
  await click(page.getByRole("button", { name: "New source" }), "New source");
  await type(page.getByPlaceholder("IT runbooks"), sourceName, "source name");
  await click(page.getByRole("button", { name: "IT Service Desk", exact: true }).filter({ visible: true }).first(), "department: IT Service Desk");
  await shot("knowledge-new-source");
  await click(page.getByRole("button", { name: "Create source" }), "Create source");
  await pause(1200);
  const runbook = [
    "# Acme IT runbook: VPN (SYNTHETIC DEMO DOCUMENT)",
    "",
    "Acme staff connect with the AcmeConnect VPN client. If AcmeConnect shows error 809, open Settings > Network,",
    "remove the profile named ACME-GW-EU, then import acme-gw-eu-2026.ovpn from the IT portal and restart the client.",
    "",
    "If error 812 appears, the account's multi-factor enrolment has expired: re-enrol at portal.acme.example/mfa.",
    "",
    "# Printers",
    "",
    "The third-floor colour printer queue is PRN-3F-COLOR and needs the Konica driver version 4.2.",
  ].join("\n");
  const f = path.join(OUT, "acme-it-runbook-SYNTHETIC.md");
  fs.writeFileSync(f, runbook);
  await page.locator('input[type="file"]').first().setInputFiles(f);
  say("upload", "acme-it-runbook-SYNTHETIC.md");
  await page.getByText(/Indexed /).waitFor({ timeout: 60_000 });
  await pause(900);
  await shot("knowledge-indexed");
  fs.unlinkSync(f);
});

// Assistant: a question answered from live data, and a task proposed + confirmed
await step("assistant", async () => {
  await visit("/assistant", "assistant");
  const box = page.getByLabel("Message the assistant");
  await type(box, "Is everything healthy right now, and what happened in the last 24 hours?", "assistant question");
  await click(page.getByRole("button", { name: "Send" }), "Send");
  await page.getByText("Looking at live data on the device…").waitFor({ state: "detached", timeout: GEN });
  await pause(1200);
  await shot("assistant-answer");
  await type(box, "Take a backup now", "assistant task");
  await click(page.getByRole("button", { name: "Send" }), "Send");
  await page.getByText("Looking at live data on the device…").waitFor({ state: "detached", timeout: GEN });
  await pause(900);
  await shot("assistant-proposal");
  const confirm = page.getByRole("button", { name: "Confirm" });
  if (await confirm.count()) {
    await click(confirm.first(), "Confirm proposed action");
    await page.getByText(/Done, recorded in the activity log/).waitFor({ timeout: 60_000 });
    await pause(800);
    await shot("assistant-confirmed");
  }
  await html("assistant");
});

// Access: create an app key (blurred), look at departments and the activity log
await step("access", async () => {
  await visit("/access", "access-keys");
  await click(page.getByRole("button", { name: "New key" }), "New key");
  await type(page.getByPlaceholder("helpdesk-bot"), "demo-video-app", "key name");
  await shot("access-new-key");
  await click(page.getByRole("button", { name: "Create key" }), "Create key");
  await page.getByText("Key created").waitFor({ timeout: 20_000 });
  await pause(700);
  await shot("access-key-created-blurred");
  await click(page.getByRole("button", { name: "Done, I saved it" }), "Done");
  await click(page.getByRole("tab", { name: "Departments" }), "Departments tab");
  await pause(1000);
  await shot("access-departments");
  await click(page.getByRole("tab", { name: "Activity log" }), "Activity log tab");
  await pause(1000);
  await shot("access-activity-log");
  const keys = (await api("GET", "/api/keys")).keys.filter((k) => k.label === "app:demo-video-app" && k.state === "active");
  cleanup.keys.push(...keys.map((k) => k.key_id));
});

// Alerts, Operations, FinOps, Policies, Cache, Router Lab, Infrastructure
await step("alerts", async () => {
  await visit("/alerts", "alerts");
  await click(page.getByRole("button", { name: "Send test alert" }), "Send test alert");
  await pause(1200);
  await shot("alerts-test");
});
await step("operations", async () => {
  await visit("/operations", "operations");
  await click(page.getByRole("button", { name: "Back up now" }), "Back up now");
  await pause(3000);
  await shot("operations-backup");
  await page.mouse.wheel(0, 900);
  await pause(900);
  await shot("operations-retention-exports");
});
await step("finops", async () => {
  await visit("/finops", "finops");
  await click(page.getByRole("tab", { name: "By department" }), "By department");
  await pause(1500);
  await shot("finops-chargeback");
});
for (const [r, name] of [["/policies", "policies"], ["/cache", "verified-cache"], ["/router-lab", "router-lab"], ["/infrastructure", "infrastructure"]]) {
  await step(name, () => visit(r, name));
}

// Employee chat app with a person key; the answer comes from the uploaded runbook
await step("chat-app", async () => {
  const k = await api("POST", "/api/keys", { tenant_id: "acme", department_id: "it", kind: "person", name: "Demo Employee", days: 1 });
  cleanup.keys.push(k.key_id);
  say("create person key (API)", `${k.label}, 1-day expiry`);
  const chat = await ctx.newPage();
  await record(chat);
  chat.on("pageerror", (e) => errors.push(String(e)));
  await chat.goto(BASE + "/chat");
  await chat.waitForTimeout(1000);
  await shot("chat-login", chat);
  await chat.getByLabel("Access key").fill(k.api_key);
  await click(chat.getByRole("button", { name: "Start chatting" }), "Start chatting", chat);
  await chat.waitForTimeout(1200);
  await shot("chat-home", chat);
  await type(chat.getByLabel("Message"), "The AcmeConnect VPN shows error 809. What should I do?", "employee question", chat);
  await click(chat.getByRole("button", { name: "Send" }), "Send", chat);
  await chat.getByText("Thinking on the device…").waitFor({ state: "detached", timeout: GEN });
  await chat.waitForTimeout(1200);
  const last = chat.locator("main .panel").last();
  say("result", `chat: ${(await last.textContent())?.slice(0, 160)} | ${(await chat.locator("main").last().innerText()).match(/Answered[^\n·]*|Blocked[^\n·]*/)?.[0] ?? ""}`);
  await shot("chat-answer", chat);
  await html("chat", chat);
  await chat.close();
  await record(page);
});

// design tokens actually rendered
const tokens = await page.evaluate(() => {
  const cs = (sel) => { const el = document.querySelector(sel); if (!el) return null; const s = getComputedStyle(el);
    return { color: s.color, background: s.backgroundColor, backgroundImage: s.backgroundImage.slice(0, 200), font: s.fontFamily,
      size: s.fontSize, weight: s.fontWeight, radius: s.borderRadius, shadow: s.boxShadow, backdrop: s.backdropFilter }; };
  return { body: cs("body"), h1: cs("h1"), panel: cs(".panel"), primaryButton: cs(".btn-primary"), ghostButton: cs(".btn-ghost"),
    input: cs(".input"), sidebar: cs("aside"), eyebrow: cs(".eyebrow"), mono: cs(".mono") };
});
fs.writeFileSync(path.join(OUT, "design-tokens.json"), JSON.stringify({
  note: "Computed styles from the live dashboard. Palette source: frontend/tailwind.config.ts + src/index.css",
  palette: { ivory: "#FAF8F4", ink: "#0F1B33", ink2: "#4A5468", ink3: "#7A8397", line: "#E4E6EB", mint: "#0E7A55", cyan: "#0B6E85",
    peri: "#3D4FB8", lilac: "#6B45B0", blush: "#A8374A", warn: "#8F5600", danger: "#B42F2F" },
  fonts: { sans: "Inter Variable", mono: "JetBrains Mono Variable" }, computed: tokens }, null, 2));

// cleanup: revoke the demo keys so nothing captured is usable
for (const id of cleanup.keys) { await api("POST", `/api/keys/${id}/revoke`); say("revoke demo key", id); }

say("done", `${n} screenshots; page errors: ${errors.length}`);
fs.writeFileSync(path.join(OUT, "actions.json"), JSON.stringify({ base: BASE, started: new Date(t0).toISOString(),
  duration_s: (Date.now() - t0) / 1000, steps: log, page_errors: errors }, null, 2));
if (rec) await rec.send("Page.stopScreencast").catch(() => {});
await ctx.close();
await browser.close();
// frame i is shown until frame i+1 arrived (screencast only emits on change); cap long idle gaps at 6 s
const list = frames.map((f, i) => {
  const d = i + 1 < frames.length ? Math.min(Math.max(frames[i + 1].ts - f.ts, 0.02), 6) : 2;
  return `file '${path.join(OUT, "frames", f.file)}'\nduration ${d.toFixed(3)}`;
}).join("\n") + (frames.length ? `\nfile '${path.join(OUT, "frames", frames.at(-1).file)}'` : "");
fs.writeFileSync(path.join(OUT, "frames.txt"), list);
const FFMPEG = process.env.FFMPEG;
if (FFMPEG && frames.length) {
  execFileSync(FFMPEG, ["-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", path.join(OUT, "frames.txt"),
    "-vf", "scale=1440:900:force_original_aspect_ratio=decrease,pad=1440:900:(ow-iw)/2:(oh-ih)/2:color=white,fps=30",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", path.join(OUT, "video", "walkthrough.mp4")]);
  fs.rmSync(path.join(OUT, "frames"), { recursive: true, force: true });
  fs.rmSync(path.join(OUT, "frames.txt"));
  console.log("video: " + path.join(OUT, "video", "walkthrough.mp4"));
} else console.log(`video frames kept in ${path.join(OUT, "frames")} (set FFMPEG to assemble)`);
