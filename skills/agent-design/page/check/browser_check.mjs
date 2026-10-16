// Browser check of the Agent Design page in headless Chrome (DevTools protocol, no packages).
// Usage: node browser_check.mjs <page.html> <screens_dir> <width>
// Runs every sub-check and prints one line "FAIL <name>: <detail>" per failure. Names: script, console-error,
// sideways-overflow, tab-panel, tab-list-out-of-view, target-under-sticky-bar, diagram-frame, fit-all.
// Exit 0: every sub-check passed. Exit 1: at least one failed. Exit 2: usage error, or Chrome could not start or be
// driven (a broken environment never passes as a planted failure). The Chrome profile is made under the system
// temporary folder and removed on every path. Built from experiment 08's check and Fast Mode's Chrome driver.
import { spawn } from 'node:child_process';
import { writeFileSync, mkdtempSync, mkdirSync, rmSync, existsSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';

const USAGE = 'usage: node browser_check.mjs <page.html> <screens_dir> <width>';
const [pageArg, outArg, widthArg] = process.argv.slice(2);
if (process.argv.length !== 5 || !/^\d+$/.test(widthArg || '') || +widthArg < 200) { console.error(USAGE); process.exit(2); }
if (!existsSync(pageArg)) { console.error(`page not found: ${pageArg}\n${USAGE}`); process.exit(2); }
const W = +widthArg, H = 900, PAGE = 'file://' + path.resolve(pageArg), OUT = path.resolve(outArg);
const CHROME = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const sleep = ms => new Promise(r => setTimeout(r, ms));

let profile = null, chrome = null;
const cleanup = () => {
  try { if (chrome && chrome.exitCode === null && chrome.signalCode === null) chrome.kill('SIGKILL'); } catch {}
  if (profile) { try { rmSync(profile, { recursive: true, force: true }); } catch {} profile = null; }
};
process.on('exit', cleanup);
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { cleanup(); process.exit(2); });
const envFail = (msg) => { console.error('ENVIRONMENT: ' + msg); cleanup(); process.exit(2); };

const failures = [];
const fail = (name, detail) => failures.push({ name, detail: typeof detail === 'string' ? detail : JSON.stringify(detail) });
const report = { page: PAGE, width: W, checks: {}, shots: [] };

let ws, id = 0;
const pending = new Map(), consoleErrors = [];
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const i = ++id; const t = setTimeout(() => { pending.delete(i); reject(new Error('timeout: ' + method)); }, 20000);
  pending.set(i, d => { clearTimeout(t); resolve(d); }); ws.send(JSON.stringify({ id: i, method, params }));
});
const ev = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.result?.exceptionDetails) throw new Error('evaluate failed: ' + r.result.exceptionDetails.text + ' in ' + expr.slice(0, 120));
  return r.result?.result?.value;
};

async function startChrome() {
  if (!existsSync(CHROME)) envFail('Chrome not found at ' + CHROME);
  const port = await new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
  profile = mkdtempSync(path.join(tmpdir(), 'ad-browser-check-'));
  chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--disable-extensions', `--window-size=${W},${H}`, 'about:blank'], { stdio: 'ignore' });
  chrome.on('error', e => envFail('Chrome could not start: ' + e.message));
  let list = null;
  for (let i = 0; i < 75 && !list; i++) {
    try { const l = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); if (l.some(t => t.type === 'page')) list = l; } catch {}
    if (!list) await sleep(200);
  }
  if (!list) envFail('Chrome did not open its debugging port');
  ws = new WebSocket(list.find(t => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.addEventListener('open', res); ws.addEventListener('error', () => rej(new Error('websocket error'))); });
  ws.addEventListener('message', m => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); return; }
    if (d.method === 'Runtime.consoleAPICalled' && d.params.type === 'error') consoleErrors.push('console.error: ' + d.params.args.map(a => a.value ?? a.description ?? '').join(' '));
    if (d.method === 'Runtime.exceptionThrown') consoleErrors.push('uncaught: ' + (d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text));
    if (d.method === 'Log.entryAdded' && d.params.entry.level === 'error') consoleErrors.push('log: ' + d.params.entry.text + ' ' + (d.params.entry.url || ''));
    if (d.method === 'Network.loadingFailed' && !d.params.canceled) consoleErrors.push('failed load: ' + d.params.errorText + ' ' + d.params.requestId);
  });
}

let shotN = 0;
async function shot(label) {
  try {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    const f = path.join(OUT, `w${W}-${String(++shotN).padStart(2, '0')}-${label}.png`);
    writeFileSync(f, Buffer.from(r.result.data, 'base64')); report.shots.push(path.basename(f));
  } catch (e) { report.shots.push(`${label}: not saved (${e.message})`); }
}

const OVERFLOW = `(() => { const de = document.scrollingElement || document.documentElement, sim = document.getElementById('simulation');
  return { doc: [de.scrollWidth, de.clientWidth], sim: sim ? [sim.scrollWidth, sim.clientWidth] : null }; })()`;
async function overflowAt(where) {
  const o = await ev(OVERFLOW);
  if (o.doc[0] > o.doc[1] + 1) fail('sideways-overflow', `${where}: document scrollWidth ${o.doc[0]} > clientWidth ${o.doc[1]}`);
  if (o.sim && o.sim[0] > o.sim[1] + 1) fail('sideways-overflow', `${where}: #simulation scrollWidth ${o.sim[0]} > clientWidth ${o.sim[1]}`);
  (report.checks.overflow ||= []).push({ where, ...o });
}
const LABELS = "[...document.querySelectorAll('.pp-tabs label')]";
const VISIBLE = "[...document.querySelectorAll('.pp-panel')].filter(p => getComputedStyle(p).display !== 'none').map(p => p.id)";
const clickTab = async i => { await ev(`${LABELS}[${i}].click()`); await sleep(250); };
const tabListInView = async (where) => {
  const r = await ev("(() => { const t = document.querySelector('.pp-tabs'); if (!t) return null; const b = t.getBoundingClientRect(); return { top: b.top, h: innerHeight }; })()");
  if (!r || !(r.top >= 0 && r.top <= r.h)) fail('tab-list-out-of-view', `${where}: ${JSON.stringify(r)}`);
};
// the tab index (0-based) whose panel holds the element matching sel, or -1
const tabOf = sel => ev(`(() => { const el = document.querySelector(${JSON.stringify(sel)}); const p = el && el.closest('.pp-panel');
  if (!p) return -1; return [...document.querySelectorAll('.pp-panel')].indexOf(p); })()`);

async function checks() {
  await send('Runtime.enable'); await send('Page.enable'); await send('Log.enable'); await send('Network.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  const loaded = new Promise(res => { const h = m => { if (JSON.parse(m.data).method === 'Page.loadEventFired') { ws.removeEventListener('message', h); res(); } }; ws.addEventListener('message', h); });
  await send('Page.navigate', { url: PAGE });
  await Promise.race([loaded, sleep(10000)]); await sleep(500);

  // script: no script element, on* handler or javascript: URL
  const ns = await ev(`(() => { const o = []; if (document.querySelectorAll('script').length) o.push(document.querySelectorAll('script').length + ' script element(s)');
    document.querySelectorAll('*').forEach(el => { for (const a of el.attributes) { if (/^on/i.test(a.name)) o.push(el.tagName.toLowerCase() + ' ' + a.name);
      if (/^\\s*javascript:/i.test(a.value)) o.push(el.tagName.toLowerCase() + ' ' + a.name + '=javascript:'); } }); return o; })()`);
  report.checks.script = ns;
  if (ns.length) fail('script', ns.slice(0, 5).join('; '));
  await overflowAt('on load'); await shot('load');

  // tab-panel: each of the seven section tabs shows exactly its panel; the tab list stays in view
  const n = await ev(`${LABELS}.length`), panels = await ev("[...document.querySelectorAll('.pp-panel')].map(p => p.id)");
  report.checks.tabs = { labels: n, panels };
  if (n !== 7 || panels.length !== 7) fail('tab-panel', `expected 7 tabs and 7 panels, found ${n} tabs and ${panels.length} panels`);
  for (let i = 0; i < Math.min(n, panels.length); i++) {
    await ev('window.scrollTo(0, 400)'); await clickTab(i);
    const vis = await ev(VISIBLE);
    if (!(vis.length === 1 && vis[0] === panels[i])) fail('tab-panel', `tab ${i + 1}: visible panels ${JSON.stringify(vis)}, expected ["${panels[i]}"]`);
    await tabListInView(`after clicking tab ${i + 1}`); await overflowAt(`tab ${i + 1}`);
    await ev('window.scrollTo(0, 0)'); await sleep(80); await shot(`tab${i + 1}`);
  }

  // target-under-sticky-bar: three cases open by :target and each heading sits below the simulation's sticky bar
  const simTab = await tabOf('#simulation');
  if (simTab < 0) fail('target-under-sticky-bar', 'no #simulation element inside a section panel');
  else {
    await clickTab(simTab);
    const links = await ev(`(() => { const sim = document.getElementById('simulation'); const seen = [];
      sim.querySelectorAll('a[href^="#"]').forEach(a => { const t = a.getAttribute('href').slice(1); const el = t && document.getElementById(t);
        if (el && sim.contains(el) && el !== sim && !seen.includes(t)) seen.push(t); }); return seen; })()`);
    report.checks.cases = { links, opened: [] };
    if (links.length < 3) fail('target-under-sticky-bar', `fewer than three case links inside #simulation (${links.length})`);
    const pick = links.length <= 3 ? links : [links[0], links[Math.floor(links.length / 2)], links[links.length - 1]];
    for (const t of pick) {
      await ev(`document.querySelector('#simulation a[href="#${t}"]').click()`); await sleep(350);
      const r = await ev(`(() => { const el = document.getElementById(${JSON.stringify(t)}), sim = document.getElementById('simulation');
        const bar = [...sim.querySelectorAll('*')].find(e => getComputedStyle(e).position === 'sticky');
        const head = el.querySelector('h1,h2,h3,h4,h5,h6'); const b = bar ? bar.getBoundingClientRect() : null;
        const det = el.querySelector('.sim-detail'); const dr = det ? det.getBoundingClientRect() : null;
        const opened = !!det && getComputedStyle(det).display !== 'none' && getComputedStyle(det).visibility !== 'hidden' && dr.width > 0 && dr.height > 0;
        return { hash: location.hash, target: el.matches(':target'), shown: getComputedStyle(el).display !== 'none' && el.getClientRects().length > 0,
          opened, detail: dr && [dr.width, dr.height], barTop: b && b.top, barBottom: b && b.bottom, barH: b && b.height, headTop: head ? head.getBoundingClientRect().top : null,
          panels: ${VISIBLE} }; })()`);
      report.checks.cases.opened.push({ id: t, ...r });
      if (!r.target || !r.shown) fail('target-under-sticky-bar', `#${t} did not open by :target (${JSON.stringify(r)})`);
      else if (!r.opened) fail('target-under-sticky-bar', `#${t} is the :target but its opened content (.sim-detail) is not displayed with a non-zero size (${JSON.stringify(r.detail)})`);
      else if (r.barH === null || !(r.barH > 0)) fail('target-under-sticky-bar', `#${t}: the simulation's sticky bar is absent or has zero height`);
      else if (r.headTop === null || r.headTop < r.barBottom - 1) fail('target-under-sticky-bar', `#${t}: heading top ${r.headTop} is under the sticky bar (bottom ${r.barBottom})`);
      if (r.panels.length !== 1) fail('tab-panel', `after opening #${t}: visible panels ${JSON.stringify(r.panels)}`);
      await overflowAt(`case #${t} open`); await shot(`case-${t}`);
    }
    await clickTab(0); await tabListInView('after a case jump and a tab click');
    const vis = await ev(VISIBLE);
    if (!(vis.length === 1 && vis[0] === panels[0])) fail('tab-panel', `after a case jump, tab 1 shows ${JSON.stringify(vis)}`);
  }

  // diagram-frame and fit-all: the frame scrolls at "Readable size"; "Fit all" shows the whole diagram
  const dgTab = await tabOf('[data-slot="agent-diagram"] svg');
  if (dgTab < 0) { fail('diagram-frame', 'no diagram SVG in the agent-diagram slot'); fail('fit-all', 'no diagram to fit'); }
  else {
    await clickTab(dgTab);
    const GEO = `(() => { const slot = document.querySelector('[data-slot="agent-diagram"]'), svg = slot.querySelector('svg');
      let f = svg.parentElement; while (f && f !== slot && !['auto', 'scroll'].includes(getComputedStyle(f).overflowX)) f = f.parentElement;
      if (!f || f === slot) return null; const fr = f.getBoundingClientRect(), s = svg.getBoundingClientRect();
      return { scrollW: f.scrollWidth, clientW: f.clientWidth, frame: [fr.left, fr.top, fr.right, fr.bottom], svg: [s.left, s.top, s.right, s.bottom],
        inside: s.left >= fr.left - 1 && s.top >= fr.top - 1 && s.right <= fr.right + 1 && s.bottom <= fr.bottom + 1 }; })()`;
    const label = text => `[...document.querySelectorAll('[data-slot="agent-diagram"] label')].find(l => l.textContent.trim() === ${JSON.stringify(text)})`;
    if (await ev(`!!${label('Readable size')}`)) { await ev(`${label('Readable size')}.click()`); await sleep(150); }
    const read = await ev(GEO); report.checks.diagramReadable = read;
    if (!read) fail('diagram-frame', 'the diagram has no scrolling frame');
    else if (!(read.scrollW > read.clientW)) fail('diagram-frame', `frame does not scroll at Readable size (scrollWidth ${read.scrollW}, clientWidth ${read.clientW})`);
    else {
      const pan = await ev(`(() => { const slot = document.querySelector('[data-slot="agent-diagram"]'); let f = slot.querySelector('svg').parentElement;
        while (!['auto', 'scroll'].includes(getComputedStyle(f).overflowX)) f = f.parentElement; f.scrollLeft = 120; const v = f.scrollLeft; f.scrollLeft = 0; return v; })()`);
      if (!(pan > 0)) fail('diagram-frame', 'scrolling the frame did not move the diagram');
    }
    await shot('diagram-readable');
    if (!(await ev(`!!${label('Fit all')}`))) fail('fit-all', 'no "Fit all" control in the agent-diagram slot');
    else {
      await ev(`${label('Fit all')}.click()`); await sleep(200);
      const fit = await ev(GEO); report.checks.diagramFitAll = fit;
      if (!fit || !fit.inside) fail('fit-all', `the diagram does not fit inside its frame: ${JSON.stringify(fit)}`);
      await shot('diagram-fit-all');
    }
    await overflowAt('diagram tab');
  }
  await sleep(200);
  report.checks.consoleErrors = consoleErrors;
  if (consoleErrors.length) fail('console-error', consoleErrors.slice(0, 5).join(' | '));
}

try { mkdirSync(OUT, { recursive: true }); } catch (e) { console.error(`cannot make ${OUT}: ${e.message}`); process.exit(2); }
try {
  await startChrome();
  await checks();
} catch (e) {
  envFail('the check could not drive Chrome: ' + (e && e.stack || e));
}
report.failures = failures; report.pass = failures.length === 0;
writeFileSync(path.join(OUT, `browser-check-${W}-${path.basename(pageArg, '.html')}.json`), JSON.stringify(report, null, 1));
try { ws.close(); } catch {}
cleanup();
for (const f of failures) console.log(`FAIL ${f.name}: ${f.detail.slice(0, 400)}`);
console.log(`${failures.length ? 'FAILED' : 'PASSED'} ${path.basename(pageArg)} at ${W} px: ${failures.length} failure(s), ${report.shots.length} screenshot(s) in ${outArg}`);
process.exit(failures.length ? 1 : 0);
