// Decode a video at a constant frame rate, split frames into cells, and find the
// nearest glyph for every distinct cell feature.
import { ALL_FORMATS, BlobSource, Input, VideoSampleSink } from '../vendor/mediabunny.min.mjs';
import { CellFeaturizer, analysisLayout, rgbaToLuma } from './features.js';
import { t } from './i18n.js';

const FEATURE_BYTES = 768;

export async function openVideo(file) {
  const input = new Input({ source: new BlobSource(file), formats: ALL_FORMATS });
  const video = await input.getPrimaryVideoTrack();
  if (!video) throw new Error(t('err.noVideoTrack'));
  if (!(await video.canDecode())) throw new Error(t('err.decode', { codec: video.codec ?? t('unknown') }));
  const audio = await input.getPrimaryAudioTrack();
  const start = await video.getFirstTimestamp();
  const end = await input.computeDuration([video]);
  const stats = await video.computePacketStats(240);
  return {
    file, input, video, audio, start, end,
    duration: end - start,
    sourceFps: stats.averagePacketRate,
    width: video.displayWidth, height: video.displayHeight,
  };
}

export function frameTimes(source, fps) {
  const count = Math.max(1, Math.round(source.duration * fps));
  // Sample the middle of each output interval: the frame shown then is the
  // one a constant-rate resample keeps (same as FFmpeg's fps filter).
  const time = (i) => source.start + (i + 0.5) / fps;
  return { count, time };
}

// Open-addressing table from a 64-bit feature hash to a distinct-query id.
class FeatureTable {
  constructor(capacity = 1 << 16) {
    this.alloc(capacity);
    this.size = 0;
  }

  alloc(capacity) {
    this.mask = capacity - 1;
    this.h1 = new Uint32Array(capacity);
    this.h2 = new Uint32Array(capacity);
    this.value = new Int32Array(capacity).fill(-1);
  }

  lookup(a, b, create) {
    if (this.size * 2 >= this.mask) this.grow();
    let slot = (a ^ Math.imul(b, 0x9e3779b1)) & this.mask;
    for (;;) {
      const v = this.value[slot];
      if (v < 0) {
        if (create < 0) return -1;
        this.h1[slot] = a; this.h2[slot] = b; this.value[slot] = create; this.size++;
        return create;
      }
      if (this.h1[slot] === a && this.h2[slot] === b) return v;
      slot = (slot + 1) & this.mask;
    }
  }

  grow() {
    const { h1, h2, value } = this;
    this.alloc((this.mask + 1) * 2);
    this.size = 0;
    for (let i = 0; i < value.length; i++) if (value[i] >= 0) this.lookup(h1[i], h2[i], value[i]);
  }
}

function hashFeature(words) {
  let a = 0x811c9dc5, b = 0x6a09e667;
  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    a = Math.imul(a ^ w, 0x01000193);
    b = Math.imul(b ^ (w >>> 16 | w << 16), 0x85ebca6b) + 0x7f4a7c15 | 0;
  }
  a ^= a >>> 15; b ^= b >>> 13;
  return [a >>> 0, Math.imul(b, 0xc2b2ae35) >>> 0];
}

class Growable {
  constructor(Type, capacity = 4096) { this.Type = Type; this.data = new Type(capacity); }
  ensure(n) {
    if (n <= this.data.length) return;
    let capacity = this.data.length;
    while (capacity < n) capacity *= 2;
    const next = new this.Type(capacity);
    next.set(this.data);
    this.data = next;
  }
}

// Paints one frame into a (columns*32k x rows*24k) canvas and computes every cell's feature.
export class FrameFeaturizer {
  constructor(columns, rows, meta) {
    this.columns = columns;
    this.rows = rows;
    this.layout = analysisLayout(columns, rows, meta.featureWidth, meta.featureHeight);
    const { width, height, cellWidth, cellHeight } = this.layout;
    this.canvas = new OffscreenCanvas(width, height);
    this.ctx = this.canvas.getContext('2d', { willReadFrequently: true, alpha: false });
    this.ctx.imageSmoothingEnabled = true;
    this.ctx.imageSmoothingQuality = 'high';
    this.cell = new CellFeaturizer(cellWidth, cellHeight, meta.featureWidth, meta.featureHeight, meta.blur);
    this.luma = new Uint8Array(width * height);
  }

  // draw(ctx, width, height) paints the frame; features are written row-major into out.
  compute(draw, out) {
    const { width, height, cellWidth, cellHeight } = this.layout;
    draw(this.ctx, width, height);
    rgbaToLuma(this.ctx.getImageData(0, 0, width, height).data, this.luma);
    for (let r = 0; r < this.rows; r++) {
      for (let c = 0; c < this.columns; c++) {
        this.cell.feature(this.luma, width, c * cellWidth, r * cellHeight, out, (r * this.columns + c) * FEATURE_BYTES);
      }
    }
    return out;
  }
}

// Match a single frame (for the settings preview): one result per cell.
export async function matchFrame({ dict, matcher, columns, rows, image, mask }) {
  const featurizer = new FrameFeaturizer(columns, rows, dict.meta);
  const features = featurizer.compute((ctx, w, h) => ctx.drawImage(image, 0, 0, w, h),
    new Uint8Array(columns * rows * FEATURE_BYTES));
  return matcher.match(features, columns * rows, mask);
}

export async function analyze({ source, dict, matcher, columns, rows, fps, mask, signal, onProgress }) {
  const cells = columns * rows;
  const { count, time } = frameTimes(source, fps);
  const featurizer = new FrameFeaturizer(columns, rows, dict.meta);
  const frameFeatures = new Uint8Array(cells * FEATURE_BYTES);
  const frameWords = new Uint32Array(frameFeatures.buffer);
  const cellQuery = new Uint32Array(count * cells);
  const table = new FeatureTable();
  const best = new Growable(Int32Array), dist = new Growable(Int32Array), blank = new Growable(Int32Array);
  const batch = matcher.batch;
  let pending = { features: new Uint8Array(batch * FEATURE_BYTES), ids: new Int32Array(batch), count: 0 };
  let spare = { features: new Uint8Array(batch * FEATURE_BYTES), ids: new Int32Array(batch), count: 0 };
  let inflight = null;
  let distinct = 0;
  let matched = 0;

  const store = (ids, n, result) => {
    for (let i = 0; i < n; i++) {
      const id = ids[i];
      best.data[id] = result.index[i];
      dist.data[id] = result.dist[i];
      blank.data[id] = result.blank[i];
    }
    matched += n;
  };
  // Double buffering: decode the next batch while the GPU matches this one.
  const flush = async () => {
    if (inflight) await inflight;
    if (!pending.count) { inflight = null; return; }
    const job = pending;
    pending = spare;
    pending.count = 0;
    spare = job;
    best.ensure(distinct); dist.ensure(distinct); blank.ensure(distinct);
    inflight = matcher.match(job.features, job.count, mask).then((result) => store(job.ids, job.count, result));
  };

  const sink = new VideoSampleSink(source.video);
  const timestamps = (function* () { for (let i = 0; i < count; i++) yield time(i); })();
  const started = performance.now();
  let frame = 0;
  let lastReport = 0;
  for await (const sample of sink.samplesAtTimestamps(timestamps)) {
    if (signal?.aborted) throw new DOMException(t('aborted'), 'AbortError');
    const base = frame * cells;
    if (!sample) {
      // Before the first decodable frame: reuse the next real frame later; use the previous if any.
      if (frame > 0) cellQuery.copyWithin(base, base - cells, base);
      else cellQuery.fill(0xffffffff, base, base + cells);
    } else {
      featurizer.compute((ctx, w, h) => sample.draw(ctx, 0, 0, w, h), frameFeatures);
      sample.close();
      for (let i = 0; i < cells; i++) {
        const [a, b] = hashFeature(frameWords.subarray(i * FEATURE_BYTES / 4, (i + 1) * FEATURE_BYTES / 4));
        let id = table.lookup(a, b, -1);
        if (id < 0) {
          id = table.lookup(a, b, distinct++);
          pending.features.set(frameFeatures.subarray(i * FEATURE_BYTES, (i + 1) * FEATURE_BYTES), pending.count * FEATURE_BYTES);
          pending.ids[pending.count++] = id;
          if (pending.count === batch) await flush();
        }
        cellQuery[base + i] = id;
      }
    }
    frame++;
    const now = performance.now();
    if (now - lastReport > 150 || frame === count) {
      lastReport = now;
      onProgress?.({ frame, count, distinct, matched, elapsed: (now - started) / 1000 });
    }
  }
  await flush();
  await flush();
  // Leading frames without a decodable sample take the first real frame.
  const firstReal = cellQuery.findIndex((v) => v !== 0xffffffff);
  if (firstReal < 0) throw new Error(t('err.noFrames'));
  if (firstReal > 0) for (let i = 0; i < firstReal; i++) cellQuery[i] = cellQuery[firstReal + (i % cells)];
  onProgress?.({ frame: count, count, distinct, matched, elapsed: (performance.now() - started) / 1000 });
  return {
    columns, rows, cells, fps, frameCount: count, time,
    cellQuery, distinct,
    best: best.data.subarray(0, distinct), dist: dist.data.subarray(0, distinct), blank: blank.data.subarray(0, distinct),
    layout: featurizer.layout, seconds: (performance.now() - started) / 1000,
  };
}

// Glyph id for every cell of every frame; BLANK (-2) where whitespace wins.
export function resolveCells(result, useBlank, BLANK) {
  const choice = new Int32Array(result.distinct);
  for (let i = 0; i < result.distinct; i++) {
    // Blank sits after all glyphs in candidate order, so it only wins strictly.
    choice[i] = useBlank && result.blank[i] < result.dist[i] ? BLANK : result.best[i];
  }
  const cells = new Int32Array(result.cellQuery.length);
  for (let i = 0; i < cells.length; i++) cells[i] = choice[result.cellQuery[i]];
  return cells;
}
