import { t } from './i18n.js';

// Static dictionary produced by `python -m badapple_unicode web-export`.
// Matching data is downloaded once and kept in Cache Storage; glyph shapes are
// fetched per candidate with HTTP Range requests only when a video uses them.

const CACHE_PREFIX = 'badapple-unicode-dictionary-';
export const BLANK = -2;

async function openCache(build) {
  if (!('caches' in self)) return null;
  try {
    for (const name of await caches.keys()) {
      if (name.startsWith(CACHE_PREFIX) && name !== CACHE_PREFIX + build) await caches.delete(name);
    }
    return await caches.open(CACHE_PREFIX + build);
  } catch {
    return null;
  }
}

async function fetchBytes(url, cache, onBytes) {
  if (cache) {
    const hit = await cache.match(url).catch(() => null);
    if (hit) {
      const buffer = await hit.arrayBuffer();
      onBytes(buffer.byteLength);
      return buffer;
    }
  }
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    length += value.byteLength;
    onBytes(value.byteLength);
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  if (cache) await cache.put(url, new Response(bytes.slice())).catch(() => {});
  return bytes.buffer;
}

function blockIndex(blocks, cp) {
  let lo = 0, hi = blocks.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (cp < blocks[mid][0]) hi = mid - 1;
    else if (cp > blocks[mid][1]) lo = mid + 1;
    else return mid;
  }
  return -1;
}

// Small part of the dictionary (meta + one code point per candidate), enough for the block picker.
export async function loadCatalog(base) {
  const metaResponse = await fetch(new URL('meta.json', base), { cache: 'no-cache' });
  if (!metaResponse.ok) throw new Error(t('err.noData', { status: metaResponse.status }));
  const meta = await metaResponse.json();
  const cache = await openCache(meta.build);
  const codepoints = new Uint32Array(await fetchBytes(new URL(meta.codepoints, base).href, cache, () => {}));
  if (codepoints.length !== meta.candidates) throw new Error(t('err.size'));
  const NONE = 0xffff;
  const blockOf = new Uint16Array(meta.candidates);
  const blockCounts = new Uint32Array(meta.blocks.length);
  for (let c = 0; c < codepoints.length; c++) {
    const b = blockIndex(meta.blocks, codepoints[c]);
    blockOf[c] = b < 0 ? NONE : b;
    if (b >= 0) blockCounts[b]++;
  }
  return { meta, base, cache, codepoints, blockOf, blockCounts };
}

// One bit per candidate: 1 when its block is selected.
export function candidateMask(catalog, selectedBlocks) {
  const { blockOf } = catalog;
  const allowed = new Uint8Array(catalog.meta.blocks.length);
  for (const b of selectedBlocks) allowed[b] = 1;
  const words = new Uint32Array(Math.ceil(blockOf.length / 32));
  let count = 0;
  for (let c = 0; c < blockOf.length; c++) {
    if (blockOf[c] !== 0xffff && allowed[blockOf[c]]) { words[c >> 5] |= 1 << (c & 31); count++; }
  }
  return { words, count };
}

export async function loadDictionary(catalog, onProgress = () => {}) {
  const { meta, base, cache } = catalog;
  const files = [meta.pca, meta.glyphIndex, ...meta.features.map((f) => f.url)];
  const total = meta.features.reduce((sum, f) => sum + f.rows * meta.dims, 0)
    + (meta.candidates + 1) * 4 + (768 + meta.dims * 768) * 4;
  let loaded = 0;
  const progress = (bytes) => { loaded += bytes; onProgress(loaded, total); };
  const buffers = await Promise.all(files.map((name) => fetchBytes(new URL(name, base).href, cache, progress)));
  const pca = new Float32Array(buffers[0]);
  const dims = meta.dims;
  const features = new Int8Array(meta.candidates * dims);
  let offset = 0;
  for (const buffer of buffers.slice(2)) {
    features.set(new Int8Array(buffer), offset);
    offset += buffer.byteLength;
  }
  if (offset !== features.length) throw new Error(t('err.size'));
  const gnorm = new Int32Array(meta.candidates);
  for (let c = 0; c < meta.candidates; c++) {
    let n = 0;
    for (let d = c * dims, end = d + dims; d < end; d++) n += features[d] * features[d];
    gnorm[c] = n;
  }
  const mean = pca.subarray(0, 768);
  const components = pca.subarray(768);
  // The blank glyph is the all-zero feature, projected like any other candidate.
  const blank = new Int8Array(dims);
  for (let d = 0; d < dims; d++) {
    let acc = 0;
    for (let k = 0; k < 768; k++) acc -= mean[k] * components[d * 768 + k];
    blank[d] = Math.max(-127, Math.min(127, Math.round(acc / meta.scale)));
  }
  return {
    ...catalog, dims, scale: meta.scale, count: meta.candidates,
    features, gnorm, mean, components, blank,
    glyphIndex: new Uint32Array(buffers[1]),
    glyphs: new Map(), wholeFiles: new Map(),
  };
}

export function unicodeBlock(dict, cp) {
  const b = blockIndex(dict.meta.blocks, cp);
  if (b < 0) return null;
  const [start, end, name] = dict.meta.blocks[b];
  return { start, end, name };
}

export function blankGlyph(dict) {
  return { id: BLANK, codepoint: dict.meta.blank.codepoint, name: dict.meta.blank.name, font: '', blank: true };
}

function fileFor(dict, position) {
  const files = dict.meta.glyphFiles;
  let lo = 0, hi = files.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (files[mid].start <= position) lo = mid; else hi = mid - 1;
  }
  return lo;
}

async function fetchRange(dict, fileIndex, start, end) {
  const file = dict.meta.glyphFiles[fileIndex];
  const whole = dict.wholeFiles.get(fileIndex);
  if (whole) return new Uint8Array(await whole, start, end - start);
  const url = new URL(file.url, dict.base);
  const response = await fetch(url, { headers: { Range: `bytes=${start}-${end - 1}` } });
  // A range of a compressed copy would report a different total size: don't trust it.
  const total = Number(/\/(\d+)$/.exec(response.headers.get('Content-Range') || '')?.[1]);
  if (response.status === 206 && total === file.bytes) return new Uint8Array(await response.arrayBuffer());
  if (response.ok) {
    // Range unsupported: download the whole file once and serve every later lookup from it.
    const promise = response.status === 206
      ? fetch(url).then((r) => { if (!r.ok) throw new Error(`${file.url}: HTTP ${r.status}`); return r.arrayBuffer(); })
      : response.arrayBuffer();
    dict.wholeFiles.set(fileIndex, promise);
    return new Uint8Array(await promise, start, end - start);
  }
  throw new Error(`${file.url}: HTTP ${response.status}`);
}

// Fetch and parse every glyph id in `ids` that is not loaded yet.
export async function ensureGlyphs(dict, ids, onProgress = () => {}) {
  const missing = [...new Set(ids)].filter((id) => id >= 0 && !dict.glyphs.has(id)).sort((a, b) => a - b);
  if (!missing.length) return;
  const index = dict.glyphIndex;
  // Merge nearby records into one request.
  const ranges = [];
  for (const id of missing) {
    const start = index[id], end = index[id + 1], file = fileFor(dict, start);
    const last = ranges[ranges.length - 1];
    if (last && last.file === file && start - last.end < 4096 && end - last.start < 262144) {
      last.end = end;
      last.ids.push(id);
    } else {
      ranges.push({ file, start, end, ids: [id] });
    }
  }
  let done = 0;
  let next = 0;
  const worker = async () => {
    while (next < ranges.length) {
      const range = ranges[next++];
      const fileStart = dict.meta.glyphFiles[range.file].start;
      const bytes = await fetchRange(dict, range.file, range.start - fileStart, range.end - fileStart);
      for (const id of range.ids) {
        const local = index[id] - range.start;
        dict.glyphs.set(id, await parseGlyph(dict, id, bytes.subarray(local, local + index[id + 1] - index[id])));
      }
      done += range.ids.length;
      onProgress(done, missing.length);
    }
  };
  await Promise.all(Array.from({ length: Math.min(8, ranges.length) }, worker));
}

async function parseGlyph(dict, id, bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const codepoint = view.getUint32(0, true);
  const fontIndex = view.getUint16(4, true);
  const kind = view.getUint8(6);
  const nameLength = view.getUint8(7);
  const aspect = view.getFloat32(8, true);
  const name = String.fromCharCode(...bytes.subarray(12, 12 + nameLength));
  const payload = bytes.subarray(12 + nameLength);
  const font = dict.meta.fonts[fontIndex];
  const glyph = { id, codepoint, name, font: font.name, license: font.license, aspect, blank: false };
  if (kind === 0) {
    glyph.path = outlinePath(payload);
    glyph.width = glyph.height = dict.meta.coordinateMax;
  } else if (kind === 1) {
    const width = payload[0], height = payload[1];
    glyph.path = bitmapPath(payload.subarray(2), width, height);
    glyph.width = width;
    glyph.height = height;
  } else {
    glyph.image = await createImageBitmap(new Blob([payload], { type: 'image/png' }));
    glyph.width = glyph.image.width;
    glyph.height = glyph.image.height;
  }
  return glyph;
}

function outlinePath(data) {
  const path = new Path2D();
  let i = 0, x = 0, y = 0;
  const next = () => {
    let shift = 0, value = 0, byte;
    do {
      byte = data[i++];
      value += (byte & 0x7f) * 2 ** shift;
      shift += 7;
    } while (byte & 0x80);
    return value % 2 === 1 ? -(value + 1) / 2 : value / 2;
  };
  const point = () => { x += next(); y += next(); return [x, y]; };
  while (i < data.length) {
    const op = data[i++];
    if (op === 0) path.moveTo(...point());
    else if (op === 1) path.lineTo(...point());
    else if (op === 2) { const a = point(); path.quadraticCurveTo(...a, ...point()); }
    else if (op === 3) { const a = point(), b = point(); path.bezierCurveTo(...a, ...b, ...point()); }
    else path.closePath();
  }
  return path;
}

function bitmapPath(bits, width, height) {
  // One path of horizontal runs: a single fill, so no seams between pixels.
  const path = new Path2D();
  const stride = Math.ceil(width / 8);
  for (let y = 0; y < height; y++) {
    let run = -1;
    for (let x = 0; x <= width; x++) {
      const on = x < width && (bits[y * stride + (x >> 3)] & (0x80 >> (x & 7)));
      if (on && run < 0) run = x;
      if (!on && run >= 0) { path.rect(run, y, x - run, 1); run = -1; }
    }
  }
  return path;
}
