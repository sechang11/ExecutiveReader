/**
 * Static server for site/, for local checking only.
 *
 * GitHub Pages serves the same directory in production; this exists because a
 * page opened from the filesystem cannot use ES modules or fetch, so a file://
 * check would prove nothing about the demo.
 */
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';

const ROOT = new URL('../site/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const TYPES = {
  '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.css': 'text/css', '.json': 'application/json', '.gz': 'application/gzip',
};

createServer(async (req, res) => {
  // Resolve the root *before* normalising: on Windows `normalize('/')` returns
  // a backslash, so a check for '/' afterwards never matches and the server
  // tries to read the directory itself.
  const requested = decodeURIComponent(req.url.split('?')[0]);
  const path = requested === '/' ? 'index.html' : normalize(requested).replace(/^[\\/]+/, '');
  const file = join(ROOT, path);
  try {
    const body = await readFile(file);
    const headers = { 'content-type': TYPES[extname(file)] ?? 'application/octet-stream' };
    // The dictionary is served pre-compressed; without this the browser
    // double-decodes and DecompressionStream gets plain text.
    if (extname(file) === '.gz') headers['content-type'] = 'application/octet-stream';
    res.writeHead(200, headers);
    res.end(body);
  } catch {
    res.writeHead(404, { 'content-type': 'text/plain' });
    res.end('not found');
  }
}).listen(8123, () => console.log('site on http://localhost:8123'));
