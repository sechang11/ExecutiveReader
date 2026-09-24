/**
 * Run tools/domtest/ in headless Chrome and exit non-zero on failure.
 *
 * The DOM tests need a browser, and a browser is the one dependency this
 * project can assume: it is a Chrome extension, and the GitHub runner ships
 * Chrome. Driving it with `--dump-dom` rather than a automation library keeps
 * the project's dependency count at zero, which is the same reason there is no
 * build step.
 *
 * `--virtual-time-budget` makes the browser run timers as fast as it can and
 * dump once they are exhausted, so the suite's waits cost nothing here.
 *
 * Run: node tools/run-domtests.mjs
 */

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const PORT = 8125;

/**
 * How much virtual time the page is given.
 *
 * Virtual time runs timers as fast as the browser can, so this is not a
 * wall-clock limit — it is a bound on how much *simulated* waiting the suite
 * may do. Exceeding it dumps the page mid-run, which reads as a broken
 * harness rather than a slow one, so the failure message below says so.
 */
const BUDGET_MS = 120000;
const URL_UNDER_TEST = `http://localhost:${PORT}/tools/domtest/`;

/** Where Chrome lives, in the order worth trying. */
const CANDIDATES = [
  process.env.CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium-browser',
  '/usr/bin/chromium',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].filter(Boolean);

const chrome = CANDIDATES.find((p) => existsSync(p));
if (!chrome) {
  console.error('No Chrome found. Set CHROME_PATH, or open the page yourself:');
  console.error(`  node tools/serve-repo.mjs   then ${URL_UNDER_TEST.replace(String(PORT), '8124')}`);
  process.exit(1);
}

const server = spawn(process.execPath, [
  fileURLToPath(new URL('./serve-repo.mjs', import.meta.url)),
], { env: { ...process.env, PORT: String(PORT) }, stdio: 'ignore' });

const stop = () => server.kill();
process.on('exit', stop);

// Wait for the port rather than sleeping a guessed interval.
for (let i = 0; i < 100; i++) {
  try {
    await fetch(`http://localhost:${PORT}/tools/domtest/`);
    break;
  } catch {
    await new Promise((r) => setTimeout(r, 100));
  }
}

const args = [
  '--headless=new',
  '--disable-gpu',
  '--no-sandbox',
  `--virtual-time-budget=${BUDGET_MS}`,
  '--dump-dom',
  URL_UNDER_TEST,
];

const dom = await new Promise((resolve, reject) => {
  const p = spawn(chrome, args, { stdio: ['ignore', 'pipe', 'ignore'] });
  let out = '';
  p.stdout.on('data', (d) => { out += d; });
  p.on('error', reject);
  p.on('close', () => resolve(out));
});

stop();

const summary = /<div id="summary"[^>]*>([^<]*)<\/div>/.exec(dom)?.[1]?.trim();

if (!summary || summary === 'Running…') {
  // Name the likely repair. This failure has exactly two causes, and the
  // difference between them is how many tests reached the page: a suite that
  // ran out of virtual time has results and no summary, while one that threw
  // on the way in has neither.
  const ran = [...dom.matchAll(/<li class="(pass|fail)">/g)].length;
  console.error(ran
    ? `The suite ran ${ran} tests and then stopped. That is usually the `
      + `virtual time budget (${BUDGET_MS} ms); raise it in this file.`
    : 'The suite never started. Chrome dumped the page below, which usually '
      + 'means a module failed to load — check the paths and the server.');
  console.error(dom.slice(0, 2000));
  process.exit(1);
}

console.log(summary);

if (/FAILED/.test(summary)) {
  // Print the failures rather than the whole document.
  for (const m of dom.matchAll(/<li class="fail">([\s\S]*?)<\/li>/g)) {
    console.error(m[1].replace(/<[^>]+>/g, '').trim());
  }
  process.exit(1);
}
