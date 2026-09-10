/**
 * Static server for the whole repository, for the DOM tests only.
 *
 * tools/serve-site.mjs deliberately serves only `site/`, because that is what
 * GitHub Pages serves and a local server with a wider root would hide a missing
 * file. The DOM tests need the opposite: they import the extension's real
 * modules and run them against real elements, so they need to reach across the
 * repository. Two servers rather than one wider one, so neither lies about the
 * other's job.
 *
 * Run: node tools/serve-repo.mjs   then open http://localhost:8124/tools/domtest/
 */
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';

const ROOT = new URL('../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const TYPES = {
  '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.css': 'text/css', '.json': 'application/json',
};

// Overridable so tools/run-domtests.mjs can start its own instance without
// colliding with one a person left running.
const PORT = Number(process.env.PORT) || 8124;

createServer(async (req, res) => {
  const requested = decodeURIComponent(req.url.split('?')[0]);
  const rel = requested.endsWith('/') ? `${requested}index.html` : requested;
  const path = normalize(rel).replace(/^[\/]+/, '');
  try {
    const body = await readFile(join(ROOT, path));
    res.writeHead(200, { 'content-type': TYPES[extname(path)] ?? 'application/octet-stream' });
    res.end(body);
  } catch {
    res.writeHead(404, { 'content-type': 'text/plain' });
    res.end('not found');
  }
}).listen(PORT, () => console.log(`repo on http://localhost:${PORT}`));
