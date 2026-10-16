// Real-browser check of diagram pages through headless Chrome (DevTools protocol, no packages).
// Usage: node knowledge/checks/browser_diagram_check.mjs PAGE.html [PAGE.html ...]
// For each page: it loads, no script runs (no <script> element, no on*= handler, no javascript: URL, and Chrome parses
// no script from the page), there is no console error or exception, and the diagram is visible (an <svg> holding
// diagram data attributes is rendered with a non-zero size and part of it on screen once scrolled to).
// Chrome: $CHROME, else the usual macOS / Linux install paths. With CI=true (or CHROME_NO_SANDBOX=1) Chrome runs with
// --no-sandbox, which the Ubuntu runners need; the pages are the plugin's own renders.
// stdout: one JSON object { check, result, pages: [{ page, pass, failures, ... }] }. Exit 0 pass, 1 fail, 2 setup error.
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';

const CHECK = 'browser-diagram-check';
const WIDTH = 1440, HEIGHT = 900;
const pages = process.argv.slice(2);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const setupError = msg => { console.log(JSON.stringify({ check: CHECK, result: 'setup-error', detail: msg })); process.exit(2); };
if (!pages.length) setupError('usage: node browser_diagram_check.mjs PAGE.html [PAGE.html ...]');
for (const p of pages) if (!existsSync(p)) setupError('no such page: ' + p);

const CANDIDATES = [process.env.CHROME, '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/opt/google/chrome/chrome', '/usr/bin/chromium',
  '/usr/bin/chromium-browser'].filter(Boolean);
const CHROME = CANDIDATES.find(p => existsSync(p));
if (!CHROME) setupError('Google Chrome not found (set CHROME); tried ' + CANDIDATES.join(', '));

const freePort = () => new Promise(res => { const s = createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });

// in-page probes, evaluated as source strings
const NO_SCRIPT = `(() => { const out = []; const n = document.querySelectorAll('script').length; if (n) out.push(n + ' script element(s)');
  document.querySelectorAll('*').forEach(el => { for (const a of el.attributes) {
    if (/^on[a-z]+$/i.test(a.name)) out.push('<' + el.tagName.toLowerCase() + ' ' + a.name + '>');
    if (/javascript:/i.test(a.value.replace(/[\\s\\u0000-\\u0020]+/g, ''))) out.push('javascript: URL on <' + el.tagName.toLowerCase() + '>'); } });
  return out.slice(0, 10); })()`;
const DIAGRAM = `(() => {
  const marked = '[data-graph-edge-id],[data-edge-id],[data-graph-node-id],[data-id][data-type]';
  const svgs = [...document.querySelectorAll('svg')].filter(s => s.querySelector(marked));
  if (!svgs.length) return { found: 0 };
  const svg = svgs[0];
  svg.scrollIntoView({ block: 'start', inline: 'start' });
  const r = svg.getBoundingClientRect();
  let hidden = null;
  for (let el = svg; el && el !== document.documentElement; el = el.parentElement) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') { hidden = el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className : ''); break; }
  }
  const onScreen = r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight;
  // what the reader actually sees at the frame's top-left: an element of the diagram, not something covering it
  const x = Math.min(innerWidth - 1, Math.max(0, r.left + Math.min(r.width, innerWidth) / 2));
  const y = Math.min(innerHeight - 1, Math.max(0, r.top + Math.min(r.height, innerHeight) / 2));
  const hit = document.elementFromPoint(x, y);
  return { found: svgs.length, width: Math.round(r.width), height: Math.round(r.height), onScreen, hidden,
           marks: svg.querySelectorAll(marked).length, topElementIsDiagram: !!(hit && (hit === svg || svg.contains(hit))) };
})()`;

// the shared viewer (knowledge/renderer/_diagram_viewer.py), driven from outside the page: the probe clicks labels
// the way a reader does; the page itself runs nothing
const VIEWER = `(() => {
  const forms = [...document.querySelectorAll('form.dv')];
  if (!forms.length) return { found: 0 };
  const out = [];
  for (const f of forms) {
    const frame = f.querySelector('.dv-frame'), svg = frame && frame.querySelector('svg');
    const w = parseFloat(getComputedStyle(f).getPropertyValue('--dv-w'));
    const lab = cls => [...f.querySelectorAll('label')].find(l => l.querySelector('input.' + cls));
    const r = { native: w };
    r.defaultChecked = !!f.querySelector('input.dv-zfit:checked');
    r.fitWidth = Math.round(svg.getBoundingClientRect().width); r.frameWidth = frame.clientWidth;
    r.fitCropped = frame.scrollWidth > frame.clientWidth + 1;
    lab('dv-z200').click(); r.w200 = Math.round(svg.getBoundingClientRect().width); r.scrolls200 = frame.scrollWidth > frame.clientWidth + 1;
    frame.scrollLeft = 200; r.panned = frame.scrollLeft > 0;
    lab('dv-z100').click(); r.w100 = Math.round(svg.getBoundingClientRect().width);
    lab('dv-z50').click(); r.w50 = Math.round(svg.getBoundingClientRect().width);
    lab('dv-zfit').click(); r.backToFit = Math.abs(svg.getBoundingClientRect().width - r.fitWidth) < 2;
    lab('dv-full').click(); const b = frame.getBoundingClientRect();
    r.fullCovers = b.width >= innerWidth * 0.95 && b.height >= innerHeight * 0.85;
    const bar = f.querySelector('.dv-bar').getBoundingClientRect(); r.barVisibleInFull = bar.top >= 0 && bar.bottom <= innerHeight;
    lab('dv-full').click(); r.fullCloses = frame.getBoundingClientRect().width < innerWidth * 0.99 || !f.querySelector('input.dv-full:checked');
    const a = f.querySelector('a.dv-open'); r.href = a ? a.getAttribute('href') : null;
    r.ids = f.querySelectorAll('.dv-bar [id]').length;
    out.push(r);
  }
  return { found: forms.length, viewers: out };
})()`;

async function checkPage(send, events, pagePath) {
  const url = 'file://' + path.resolve(pagePath);
  const failures = [];
  events.length = 0;
  const loaded = new Promise(r => { events.onLoad = r; });
  await send('Page.navigate', { url });
  const ok = await Promise.race([loaded.then(() => true), sleep(20000).then(() => false)]);
  if (!ok) failures.push('the page did not finish loading within 20 s');
  await sleep(300);
  const ev = async expr => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true });
    if (r.result?.exceptionDetails) { failures.push('probe failed: ' + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text)); return null; }
    return r.result?.result?.value;
  };
  const report = { page: pagePath };
  report.readyState = await ev('document.readyState');
  report.title = await ev('document.title');
  if (report.readyState !== 'complete') failures.push('document.readyState is ' + report.readyState);
  report.noScript = await ev(NO_SCRIPT) || [];
  for (const f of report.noScript) failures.push('page carries script: ' + f);
  report.scriptsParsed = events.filter(e => e.kind === 'script' && e.url && e.url.startsWith('file:')).map(e => e.url);
  if (report.scriptsParsed.length) failures.push(report.scriptsParsed.length + ' script(s) ran from the page');
  report.diagram = await ev(DIAGRAM);
  const d = report.diagram || {};
  if (!d.found) failures.push('no diagram <svg> with diagram data attributes on the page');
  else {
    if (d.hidden) failures.push('the diagram is hidden by ' + d.hidden);
    if (!(d.width > 0 && d.height > 0)) failures.push('the diagram has no size: ' + d.width + 'x' + d.height);
    if (!d.onScreen) failures.push('the diagram is not on screen when scrolled to');
    if (!d.topElementIsDiagram) failures.push('something covers the diagram at the centre of its visible part');
  }
  report.viewer = await ev(VIEWER);
  const v = report.viewer || {};
  if (!v.found) failures.push('no shared diagram viewer (form.dv) on the page');
  for (const r of v.viewers || []) {
    const near = (a, b) => Math.abs(a - b) <= Math.max(2, b * 0.01);
    if (!r.defaultChecked) failures.push('viewer: Fit width is not the default');
    if (r.fitCropped) failures.push('viewer: the default view is cropped (frame scrolls sideways at Fit width)');
    if (!near(r.fitWidth, r.frameWidth)) failures.push(`viewer: Fit width shows ${r.fitWidth}px in a ${r.frameWidth}px frame`);
    if (!near(r.w100, r.native)) failures.push(`viewer: 100% is ${r.w100}px, drawn size ${r.native}px`);
    if (!near(r.w200, 2 * r.native)) failures.push(`viewer: 200% is ${r.w200}px, want ${2 * r.native}px`);
    if (!near(r.w50, r.native / 2)) failures.push(`viewer: 50% is ${r.w50}px, want ${r.native / 2}px`);
    if (!r.scrolls200 || !r.panned) failures.push('viewer: at 200% the frame does not scroll to pan');
    if (!r.backToFit) failures.push('viewer: Fit width does not restore the fitted view');
    if (!r.fullCovers) failures.push('viewer: Full window does not cover the window');
    if (!r.barVisibleInFull) failures.push('viewer: the controls are not visible in Full window');
    if (!r.fullCloses) failures.push('viewer: Full window does not close');
    if (r.ids) failures.push('viewer: controls carry ids (two diagrams on one page would clash)');
    if (r.href !== null && !existsSync(path.resolve(path.dirname(path.resolve(pagePath)), decodeURI(r.href))))
      failures.push('viewer: the standalone SVG link does not resolve: ' + r.href);
  }
  report.errors = events.filter(e => e.kind === 'error').map(e => e.text);
  for (const e of report.errors) failures.push(e);
  report.failures = failures;
  report.pass = failures.length === 0;
  return report;
}

const port = await freePort();
const profile = mkdtempSync(path.join(tmpdir(), 'chr-diagram-'));
const flags = ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, '--no-first-run',
  '--no-default-browser-check', '--disable-gpu', `--window-size=${WIDTH},${HEIGHT}`, 'about:blank'];
if (process.env.CI === 'true' || process.env.CHROME_NO_SANDBOX === '1') flags.unshift('--no-sandbox');
const chrome = spawn(CHROME, flags, { stdio: 'ignore' });
const cleanup = () => { try { chrome.kill('SIGKILL'); } catch {} try { rmSync(profile, { recursive: true, force: true }); } catch {} };
process.on('exit', cleanup);

let list = null;
for (let i = 0; i < 100; i++) {
  try { list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); if (list.some(t => t.type === 'page')) break; } catch {}
  await sleep(200);
}
if (!list || !list.some(t => t.type === 'page')) setupError('headless Chrome did not start: ' + CHROME);
const ws = new WebSocket(list.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.addEventListener('open', res); ws.addEventListener('error', rej); });
let id = 0; const pending = new Map(); const events = [];
ws.addEventListener('message', m => {
  const d = JSON.parse(m.data);
  if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); return; }
  if (d.method === 'Page.loadEventFired' && events.onLoad) events.onLoad();
  if (d.method === 'Debugger.scriptParsed') events.push({ kind: 'script', url: d.params.url });
  if (d.method === 'Runtime.exceptionThrown') events.push({ kind: 'error', text: 'exception: ' + d.params.exceptionDetails.text });
  if (d.method === 'Runtime.consoleAPICalled' && d.params.type === 'error') events.push({ kind: 'error', text: 'console.error ' + JSON.stringify(d.params.args.map(a => a.value ?? a.description)) });
  if (d.method === 'Log.entryAdded' && d.params.entry.level === 'error') events.push({ kind: 'error', text: 'log: ' + d.params.entry.text + ' ' + (d.params.entry.url || '') });
});
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable'); await send('Debugger.enable');
await send('Emulation.setDeviceMetricsOverride', { width: WIDTH, height: HEIGHT, deviceScaleFactor: 1, mobile: false });

const results = [];
for (const p of pages) results.push(await checkPage(send, events, p));
ws.close();
const pass = results.every(r => r.pass);
console.log(JSON.stringify({ check: CHECK, result: pass ? 'pass' : 'fail', chrome: CHROME, pages: results }, null, 1));
for (const r of results) for (const f of r.failures) console.error(`FAIL ${r.page}: ${f}`);
cleanup();
process.exit(pass ? 0 : 1);
