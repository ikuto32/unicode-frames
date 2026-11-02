import { t } from './i18n.js';

// Static dictionary produced by `python -m badapple_unicode web-export`.
// Every data file is downloaded once, with the visitor's permission, and kept in
// Cache Storage. Glyph shapes are then read by slicing the stored files, so the
// host needs no HTTP Range support.

const CACHE_PREFIX = 'badapple-unicode-dictionary-';
export const BLANK = -2;

export async function loadMeta(base) {
  const response = await fetch(new URL('meta.json', base), { cache: 'no-cache' });
  if (!response.ok) throw new Error(t('err.noData', { status: response.status }));
  return response.json();
}

// Every file the app needs, with its exact size.
export function dataFiles(meta) {
  return [
    { url: meta.pca, bytes: (768 + meta.dims * 768) * 4 },
    { url: meta.glyphIndex, bytes: (meta.candidates + 1) * 4 },
    { url: meta.codepoints, bytes: meta.candidates * 4 },
    ...meta.features.map((f) => ({ url: f.url, bytes: f.rows * meta.dims })),
    ...meta.glyphFiles.map((f) => ({ url: f.url, bytes: f.bytes })),
  ];
}

// Cache Storage for this build (older builds are removed); a Map in memory when unavailable.
export async function openStore(meta, base) {
  const store = { base, cache: null, memory: new Map() };
  if ('caches' in self) {
    try {
      for (const name of await caches.keys()) {
        if (name.startsWith(CACHE_PREFIX) && name !== CACHE_PREFIX + meta.build) await caches.delete(name);
      }
      store.cache = await caches.open(CACHE_PREFIX + meta.build);
    } catch {
      store.cache = null;
    }
  }
  return store;
}

const fileUrl = (store, name) => new URL(name, store.base).href;

async function stored(store, name) {
  const url = fileUrl(store, name);
  if (store.memory.has(url)) return store.memory.get(url);
  const hit = store.cache ? await store.cache.match(url).catch(() => null) : null;
  return hit ? hit.blob() : null;
}

export async function missingFiles(store, files) {
  const missing = [];
  for (const file of files) {
    const blob = await stored(store, file.url);
    if (!blob || blob.size !== file.bytes) missing.push(file);
  }
  return missing;
}

export async function deleteStoredData() {
  if (!('caches' in self)) return;
  for (const name of await caches.keys()) if (name.startsWith(CACHE_PREFIX)) await caches.delete(name);
}

export async function downloadFiles(store, files, onProgress = () => {}, signal) {
  const total = files.reduce((sum, f) => sum + f.bytes, 0);
  let loaded = 0;
  let next = 0;
  const worker = async () => {
    while (next < files.length) {
      const file = files[next++];
      const url = fileUrl(store, file.url);
      const response = await fetch(url, { cache: 'no-cache', signal });
      if (!response.ok) throw new Error(`${file.url}: HTTP ${response.status}`);
      const reader = response.body.getReader();
      const chunks = [];
      let size = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        size += value.byteLength;
        loaded += value.byteLength;
        onProgress(loaded, total);
      }
      if (size !== file.bytes) throw new Error(t('err.size'));
      const blob = new Blob(chunks);
      let kept = false;
      if (store.cache) {
        try {
          await store.cache.put(url, new Response(blob, { headers: { 'Content-Length': String(size) } }));
          kept = true;
        } catch { /* quota exceeded: keep it in memory for this visit */ }
      }
      if (!kept) store.memory.set(url, blob);
    }
  };
  await Promise.all(Array.from({ length: Math.min(3, files.length) }, worker));
  return { persistent: store.memory.size === 0 };
}

async function storedBytes(store, name) {
  const blob = await stored(store, name);
  if (!blob) throw new Error(t('err.size'));
  return blob.arrayBuffer();
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

// Small part of the dictionary (one code point per candidate), enough for the block picker.
export async function loadCatalog(meta, store) {
  const codepoints = new Uint32Array(await storedBytes(store, meta.codepoints));
  if (codepoints.length !== meta.candidates) throw new Error(t('err.size'));
  const NONE = 0xffff;
  const blockOf = new Uint16Array(meta.candidates);
  const blockCounts = new Uint32Array(meta.blocks.length);
  for (let c = 0; c < codepoints.length; c++) {
    const b = blockIndex(meta.blocks, codepoints[c]);
    blockOf[c] = b < 0 ? NONE : b;
    if (b >= 0) blockCounts[b]++;
  }
  return { meta, store, codepoints, blockOf, blockCounts };
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

export async function loadDictionary(catalog) {
  const { meta, store } = catalog;
  const [pcaBuffer, indexBuffer, ...featureBuffers] = await Promise.all(
    [meta.pca, meta.glyphIndex, ...meta.features.map((f) => f.url)].map((name) => storedBytes(store, name)));
  const pca = new Float32Array(pcaBuffer);
  const dims = meta.dims;
  const features = new Int8Array(meta.candidates * dims);
  let offset = 0;
  for (const buffer of featureBuffers) {
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
  // Stored glyph files as Blobs: slicing reads only the needed bytes.
  const glyphBlobs = await Promise.all(meta.glyphFiles.map((f) => stored(store, f.url)));
  if (glyphBlobs.some((blob, i) => !blob || blob.size !== meta.glyphFiles[i].bytes)) throw new Error(t('err.size'));
  return {
    ...catalog, dims, scale: meta.scale, count: meta.candidates,
    features, gnorm, mean, components, blank,
    glyphIndex: new Uint32Array(indexBuffer), glyphBlobs,
    glyphs: new Map(),
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

// Parse every glyph id in `ids` that is not loaded yet, reading nearby records together.
export async function ensureGlyphs(dict, ids, onProgress = () => {}) {
  const missing = [...new Set(ids)].filter((id) => id >= 0 && !dict.glyphs.has(id)).sort((a, b) => a - b);
  if (!missing.length) return;
  const index = dict.glyphIndex;
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
  for (const range of ranges) {
    const fileStart = dict.meta.glyphFiles[range.file].start;
    const bytes = new Uint8Array(await dict.glyphBlobs[range.file]
      .slice(range.start - fileStart, range.end - fileStart).arrayBuffer());
    for (const id of range.ids) {
      const local = index[id] - range.start;
      dict.glyphs.set(id, await parseGlyph(dict, id, bytes.subarray(local, local + index[id + 1] - index[id])));
    }
    done += range.ids.length;
    onProgress(done, missing.length);
  }
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
