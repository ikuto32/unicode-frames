// Minimal static server for web/ (local testing). Also answers HTTP Range requests.
// Usage: node tools/serve-web.mjs [port]
import { createReadStream, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('../web/', import.meta.url)));
const port = Number(process.argv[2] || 8000);
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.css': 'text/css', '.json': 'application/json', '.bin': 'application/octet-stream', '.txt': 'text/plain; charset=utf-8' };

createServer((req, res) => {
  const path = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  let file = normalize(join(root, path));
  if (!file.startsWith(root)) { res.writeHead(403).end(); return; }
  let stat;
  try { stat = statSync(file); if (stat.isDirectory()) { file = join(file, 'index.html'); stat = statSync(file); } }
  catch { res.writeHead(404).end('Not found'); return; }
  const headers = { 'Content-Type': types[extname(file)] || 'application/octet-stream', 'Accept-Ranges': 'bytes', 'Cache-Control': 'no-cache' };
  const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range || '');
  if (range) {
    const start = Number(range[1]), end = range[2] ? Math.min(Number(range[2]), stat.size - 1) : stat.size - 1;
    if (start > end) { res.writeHead(416, { 'Content-Range': `bytes */${stat.size}` }).end(); return; }
    res.writeHead(206, { ...headers, 'Content-Range': `bytes ${start}-${end}/${stat.size}`, 'Content-Length': end - start + 1 });
    createReadStream(file, { start, end }).pipe(res);
  } else {
    res.writeHead(200, { ...headers, 'Content-Length': stat.size });
    createReadStream(file).pipe(res);
  }
}).listen(port, () => console.log(`Serving ${root} at http://localhost:${port}/`));
