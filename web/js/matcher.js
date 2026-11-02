// Exhaustive nearest-glyph search over all candidates in the PCA int8 space.
// Distances are exact integers: |q|^2 + |g|^2 - 2 q.g, ties go to the lower candidate id.
// match() returns, per query: best candidate, its distance, and the distance to the blank glyph.

const FEATURE_BYTES = 768;
const MAX_I32 = 0x7fffffff;

export function quantizeQueries(dict, features, count) {
  // CPU projection; the GPU path does the same in a shader.
  const { mean, components, dims, scale } = dict;
  const out = new Int8Array(count * dims);
  const centered = new Float32Array(FEATURE_BYTES);
  for (let q = 0; q < count; q++) {
    const base = q * FEATURE_BYTES;
    for (let k = 0; k < FEATURE_BYTES; k++) centered[k] = features[base + k] - mean[k];
    for (let d = 0; d < dims; d++) {
      const row = d * FEATURE_BYTES;
      let acc = 0;
      for (let k = 0; k < FEATURE_BYTES; k++) acc += centered[k] * components[row + k];
      out[q * dims + d] = clampRound(acc / scale);
    }
  }
  return out;
}

export function clampRound(value) {
  // numpy.round: half to even
  let r = Math.round(value);
  if (Math.abs(value % 1) === 0.5 && r % 2 !== 0) r -= 1;
  return Math.max(-127, Math.min(127, r));
}

function blankDistances(dict, quantized, count) {
  const dims = dict.dims, blank = dict.blank;
  const qnorm = new Int32Array(count), blankDist = new Int32Array(count);
  for (let q = 0; q < count; q++) {
    let n = 0, b = 0;
    for (let d = 0; d < dims; d++) {
      const v = quantized[q * dims + d];
      n += v * v;
      const e = v - blank[d];
      b += e * e;
    }
    qnorm[q] = n;
    blankDist[q] = b;
  }
  return { qnorm, blankDist };
}

function searchShader(packedDot) {
  const dp = packedDot
    ? 'fn dp(a: u32, b: u32) -> i32 { return dot4I8Packed(a, b); }'
    : `fn sx(v: u32, s: u32) -> i32 { return bitcast<i32>(v << s) >> 24u; }
fn dp(a: u32, b: u32) -> i32 {
  return sx(a, 24u) * sx(b, 24u) + sx(a, 16u) * sx(b, 16u) + sx(a, 8u) * sx(b, 8u) + sx(a, 0u) * sx(b, 0u);
}`;
  const lines = [];
  for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) lines.push(`acc${i}${j} += dp(a${i}, b${j});`);
  const zero = [];
  for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) zero.push(`var acc${i}${j} = 0;`);
  const update = [];
  for (let j = 0; j < 4; j++) {
    update.push(`{ let c = cbase + ${j * 16}u + tx; if (c < p.n && ((allowed[c >> 5u] >> (c & 31u)) & 1u) == 1u) { let gn = gnorm[c];`);
    for (let i = 0; i < 4; i++) update.push(`  { let d = gn - 2 * acc${i}${j}; if (d < best${i}) { best${i} = d; bidx${i} = i32(c); } }`);
    update.push('} }');
  }
  return `${packedDot ? 'requires packed_4x8_integer_dot_product;' : ''}
struct Params { count: u32, n: u32, tiles: u32, tilesPerSplit: u32 };
@group(0) @binding(0) var<storage, read> cand: array<u32>;
@group(0) @binding(1) var<storage, read> gnorm: array<i32>;
@group(0) @binding(2) var<storage, read> queries: array<u32>;
@group(0) @binding(3) var<storage, read_write> partial: array<vec2<i32>>;
@group(0) @binding(4) var<uniform> p: Params;
@group(0) @binding(5) var<storage, read> allowed: array<u32>;
${dp}
// k-major tiles: element (row r, word k) at [k * 64 + r]; threads read consecutive words.
var<workgroup> qs: array<u32, 2048>;
var<workgroup> cs: array<u32, 2048>;
@compute @workgroup_size(16, 16)
fn main(@builtin(local_invocation_id) lid: vec3<u32>, @builtin(workgroup_id) wid: vec3<u32>) {
  let tx = lid.x; let ty = lid.y; let t = ty * 16u + tx;
  let qbase = wid.x * 64u; let split = wid.y;
  for (var i = 0u; i < 8u; i++) {
    let e = t + i * 256u; let r = e % 64u; let k = e / 64u; let q = qbase + r;
    var v = 0u; if (q < p.count) { v = queries[q * 32u + k]; }
    qs[k * 64u + r] = v;
  }
  var best0 = ${MAX_I32}; var best1 = ${MAX_I32}; var best2 = ${MAX_I32}; var best3 = ${MAX_I32};
  var bidx0 = -1; var bidx1 = -1; var bidx2 = -1; var bidx3 = -1;
  let t0 = split * p.tilesPerSplit; let t1 = min(t0 + p.tilesPerSplit, p.tiles);
  for (var tile = t0; tile < t1; tile++) {
    let cbase = tile * 64u;
    workgroupBarrier();
    for (var i = 0u; i < 8u; i++) {
      let e = t + i * 256u; let r = e % 64u; let k = e / 64u; let c = cbase + r;
      var v = 0u; if (c < p.n) { v = cand[c * 32u + k]; }
      cs[k * 64u + r] = v;
    }
    workgroupBarrier();
    ${zero.join(' ')}
    for (var k = 0u; k < 32u; k++) {
      let o = k * 64u;
      let a0 = qs[o + ty]; let a1 = qs[o + 16u + ty]; let a2 = qs[o + 32u + ty]; let a3 = qs[o + 48u + ty];
      let b0 = cs[o + tx]; let b1 = cs[o + 16u + tx]; let b2 = cs[o + 32u + tx]; let b3 = cs[o + 48u + tx];
      ${lines.join('\n      ')}
    }
    // Candidates are visited in increasing order per thread, so "<" keeps the earliest tie.
    ${update.join('\n    ')}
  }
  workgroupBarrier();
  cs[(0u * 16u + ty) * 16u + tx] = bitcast<u32>(best0); cs[1024u + (0u * 16u + ty) * 16u + tx] = bitcast<u32>(bidx0);
  cs[(1u * 16u + ty) * 16u + tx] = bitcast<u32>(best1); cs[1024u + (1u * 16u + ty) * 16u + tx] = bitcast<u32>(bidx1);
  cs[(2u * 16u + ty) * 16u + tx] = bitcast<u32>(best2); cs[1024u + (2u * 16u + ty) * 16u + tx] = bitcast<u32>(bidx2);
  cs[(3u * 16u + ty) * 16u + tx] = bitcast<u32>(best3); cs[1024u + (3u * 16u + ty) * 16u + tx] = bitcast<u32>(bidx3);
  workgroupBarrier();
  if (tx == 0u) {
    for (var i = 0u; i < 4u; i++) {
      let row = i * 16u + ty; let q = qbase + row;
      if (q < p.count) {
        var bd = ${MAX_I32}; var bi = -1;
        for (var s = 0u; s < 16u; s++) {
          let d = bitcast<i32>(cs[row * 16u + s]); let id = bitcast<i32>(cs[1024u + row * 16u + s]);
          if (id >= 0 && (bi < 0 || d < bd || (d == bd && id < bi))) { bd = d; bi = id; }
        }
        partial[split * p.count + q] = vec2<i32>(bd, bi);
      }
    }
  }
}`;
}

const PROJECT_SHADER = `
struct Params { count: u32, scale: f32 };
@group(0) @binding(0) var<storage, read> feats: array<u32>;
@group(0) @binding(1) var<storage, read> comps: array<f32>;
@group(0) @binding(2) var<storage, read> mean: array<f32>;
@group(0) @binding(3) var<storage, read_write> outq: array<u32>;
@group(0) @binding(4) var<uniform> p: Params;
@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
  let q = gid.x / 32u; let g = gid.x % 32u;
  if (q >= p.count) { return; }
  var acc = vec4<f32>(0.0);
  let r0 = (g * 4u) * 768u;
  for (var w = 0u; w < 192u; w++) {
    let packed = feats[q * 192u + w];
    for (var b = 0u; b < 4u; b++) {
      let k = w * 4u + b;
      let v = f32((packed >> (8u * b)) & 255u) - mean[k];
      acc += v * vec4<f32>(comps[r0 + k], comps[r0 + 768u + k], comps[r0 + 1536u + k], comps[r0 + 2304u + k]);
    }
  }
  let r = vec4<i32>(clamp(round(acc / p.scale), vec4<f32>(-127.0), vec4<f32>(127.0)));
  outq[q * 32u + g] = (bitcast<u32>(r.x) & 255u) | ((bitcast<u32>(r.y) & 255u) << 8u)
    | ((bitcast<u32>(r.z) & 255u) << 16u) | ((bitcast<u32>(r.w) & 255u) << 24u);
}`;

const REDUCE_SHADER = `
struct Params { count: u32, splits: u32 };
@group(0) @binding(0) var<storage, read> partial: array<vec2<i32>>;
@group(0) @binding(1) var<storage, read> queries: array<u32>;
@group(0) @binding(2) var<storage, read> blank: array<u32>;
@group(0) @binding(3) var<storage, read_write> result: array<vec4<i32>>;
@group(0) @binding(4) var<uniform> p: Params;
fn sx(v: u32, s: u32) -> i32 { return bitcast<i32>(v << s) >> 24u; }
@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
  let q = gid.x;
  if (q >= p.count) { return; }
  var best = vec2<i32>(${MAX_I32}, -1);
  for (var s = 0u; s < p.splits; s++) {
    let v = partial[s * p.count + q];
    if (v.y >= 0 && (best.y < 0 || v.x < best.x || (v.x == best.x && v.y < best.y))) { best = v; }
  }
  var qn = 0; var bd = 0;
  for (var w = 0u; w < 32u; w++) {
    let a = queries[q * 32u + w]; let b = blank[w];
    for (var s = 0u; s < 32u; s += 8u) {
      let x = sx(a, 24u - s); let y = sx(b, 24u - s);
      qn += x * x; bd += (x - y) * (x - y);
    }
  }
  result[q] = vec4<i32>(best.y, best.x + qn, bd, 0);
}`;

export class GpuMatcher {
  static async create(dict, batch = 8192) {
    if (!navigator.gpu) return null;
    const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
    if (!adapter) return null;
    const need = dict.features.byteLength;
    if (adapter.limits.maxStorageBufferBindingSize < need || adapter.limits.maxBufferSize < need) return null;
    const device = await adapter.requestDevice({ requiredLimits: {
      maxStorageBufferBindingSize: adapter.limits.maxStorageBufferBindingSize,
      maxBufferSize: adapter.limits.maxBufferSize,
    } });
    const packedDot = navigator.gpu.wgslLanguageFeatures?.has('packed_4x8_integer_dot_product') ?? false;
    return new GpuMatcher(device, dict, batch, packedDot, adapter.info?.description || adapter.info?.vendor || '');
  }

  constructor(device, dict, batch, packedDot, name) {
    this.device = device;
    this.dict = dict;
    this.batch = batch;
    this.packedDot = packedDot;
    this.label = `WebGPU${name ? ` (${name})` : ''}${packedDot ? ' · int8 dot' : ''}`;
    this.lost = false;
    device.lost.then(() => { this.lost = true; });
    const S = GPUBufferUsage.STORAGE, D = GPUBufferUsage.COPY_DST;
    const upload = (data, usage) => {
      const buffer = device.createBuffer({ size: Math.ceil(data.byteLength / 4) * 4, usage: usage | D });
      device.queue.writeBuffer(buffer, 0, data.buffer, data.byteOffset, data.byteLength);
      return buffer;
    };
    this.n = dict.count;
    this.tiles = Math.ceil(this.n / 64);
    this.cand = upload(dict.features, S);
    this.gnorm = upload(dict.gnorm, S);
    this.comps = upload(dict.components, S);
    this.mean = upload(dict.mean, S);
    this.blank = upload(new Uint8Array(dict.blank.buffer, dict.blank.byteOffset, dict.blank.byteLength), S);
    this.allowed = device.createBuffer({ size: Math.ceil(this.n / 32) * 4, usage: S | D });
    this.maskWords = null;
    this.feats = device.createBuffer({ size: batch * FEATURE_BYTES, usage: S | D });
    this.queries = device.createBuffer({ size: batch * 128, usage: S });
    this.maxSplits = 128;
    this.partial = device.createBuffer({ size: batch * this.maxSplits * 8, usage: S });
    this.result = device.createBuffer({ size: batch * 16, usage: S | GPUBufferUsage.COPY_SRC });
    this.readback = device.createBuffer({ size: batch * 16, usage: GPUBufferUsage.MAP_READ | D });
    this.projectParams = device.createBuffer({ size: 16, usage: GPUBufferUsage.UNIFORM | D });
    this.searchParams = device.createBuffer({ size: 16, usage: GPUBufferUsage.UNIFORM | D });
    this.reduceParams = device.createBuffer({ size: 16, usage: GPUBufferUsage.UNIFORM | D });
    const pipeline = (code) => device.createComputePipeline({ layout: 'auto',
      compute: { module: device.createShaderModule({ code }), entryPoint: 'main' } });
    this.projectPipeline = pipeline(PROJECT_SHADER);
    this.searchPipeline = pipeline(searchShader(this.packedDot));
    this.reducePipeline = pipeline(REDUCE_SHADER);
    const group = (pipe, buffers) => device.createBindGroup({ layout: pipe.getBindGroupLayout(0),
      entries: buffers.map((buffer, binding) => ({ binding, resource: { buffer } })) });
    this.projectGroup = group(this.projectPipeline, [this.feats, this.comps, this.mean, this.queries, this.projectParams]);
    this.searchGroup = group(this.searchPipeline, [this.cand, this.gnorm, this.queries, this.partial, this.searchParams, this.allowed]);
    this.reduceGroup = group(this.reducePipeline, [this.partial, this.queries, this.blank, this.result, this.reduceParams]);
    this.busy = Promise.resolve();
  }

  // mask: Uint32Array with one bit per candidate (see candidateMask), or null for all.
  match(features, count, mask = null) {
    // Serialize: one set of GPU buffers is reused for every batch.
    const run = this.busy.then(() => this._match(features, count, mask ?? this.allMask()));
    this.busy = run.catch(() => {});
    return run;
  }

  allMask() {
    if (!this._all) this._all = new Uint32Array(Math.ceil(this.n / 32)).fill(0xffffffff);
    return this._all;
  }

  async _match(features, count, mask) {
    if (this.lost) throw new Error('GPU device was lost');
    const device = this.device;
    if (mask !== this.maskWords) {
      device.queue.writeBuffer(this.allowed, 0, mask.buffer, mask.byteOffset, mask.byteLength);
      this.maskWords = mask;
    }
    device.queue.writeBuffer(this.feats, 0, features.buffer, features.byteOffset, count * FEATURE_BYTES);
    const workgroupsX = Math.ceil(count / 64);
    // Enough splits along the candidate axis to keep the GPU busy for small batches.
    const splits = Math.max(1, Math.min(this.maxSplits, Math.ceil(4096 / workgroupsX), this.tiles));
    const tilesPerSplit = Math.ceil(this.tiles / splits);
    const params = new ArrayBuffer(16);
    new Uint32Array(params)[0] = count;
    new Float32Array(params)[1] = this.dict.scale;
    device.queue.writeBuffer(this.projectParams, 0, params);
    device.queue.writeBuffer(this.searchParams, 0, new Uint32Array([count, this.n, this.tiles, tilesPerSplit]));
    device.queue.writeBuffer(this.reduceParams, 0, new Uint32Array([count, splits]));
    const encoder = device.createCommandEncoder();
    let pass = encoder.beginComputePass();
    pass.setPipeline(this.projectPipeline);
    pass.setBindGroup(0, this.projectGroup);
    pass.dispatchWorkgroups(Math.ceil(count * 32 / 64));
    pass.setPipeline(this.searchPipeline);
    pass.setBindGroup(0, this.searchGroup);
    pass.dispatchWorkgroups(workgroupsX, splits);
    pass.setPipeline(this.reducePipeline);
    pass.setBindGroup(0, this.reduceGroup);
    pass.dispatchWorkgroups(Math.ceil(count / 64));
    pass.end();
    encoder.copyBufferToBuffer(this.result, 0, this.readback, 0, count * 16);
    device.queue.submit([encoder.finish()]);
    await this.readback.mapAsync(GPUMapMode.READ, 0, count * 16);
    const values = new Int32Array(this.readback.getMappedRange(0, count * 16).slice(0));
    this.readback.unmap();
    const index = new Int32Array(count), dist = new Int32Array(count), blank = new Int32Array(count);
    for (let q = 0; q < count; q++) {
      index[q] = values[q * 4];
      dist[q] = values[q * 4 + 1];
      blank[q] = values[q * 4 + 2];
    }
    return { index, dist, blank };
  }

  destroy() {
    this.device.destroy();
  }
}

export class CpuMatcher {
  static create(dict, batch = 512) {
    return new CpuMatcher(dict, batch);
  }

  constructor(dict, batch) {
    this.dict = dict;
    this.batch = batch;
    const workers = Math.max(1, Math.min(navigator.hardwareConcurrency || 4, 16) - 1);
    this.label = `CPU (${workers} workers)`;
    const rows = Math.ceil(dict.count / workers);
    this.workers = [];
    for (let w = 0; w < workers; w++) {
      const start = w * rows, end = Math.min(dict.count, start + rows);
      if (start >= end) break;
      const worker = new Worker(new URL('./cpu-worker.js', import.meta.url), { type: 'module' });
      const shard = dict.features.slice(start * dict.dims, end * dict.dims);
      const norms = dict.gnorm.slice(start, end);
      worker.postMessage({ type: 'init', features: shard, norms, base: start, dims: dict.dims },
        [shard.buffer, norms.buffer]);
      this.workers.push(worker);
    }
    this.busy = Promise.resolve();
  }

  match(features, count, mask = null) {
    const run = this.busy.then(() => this._match(features, count, mask));
    this.busy = run.catch(() => {});
    return run;
  }

  async _match(features, count, mask) {
    if (mask !== this.maskWords) {
      for (const worker of this.workers) worker.postMessage({ type: 'mask', mask });
      this.maskWords = mask;
    }
    const quantized = quantizeQueries(this.dict, features, count);
    const { qnorm, blankDist } = blankDistances(this.dict, quantized, count);
    const parts = await Promise.all(this.workers.map((worker) => new Promise((resolve, reject) => {
      worker.onmessage = (event) => resolve(event.data);
      worker.onerror = reject;
      worker.postMessage({ type: 'match', queries: quantized, count });
    })));
    const index = new Int32Array(count), dist = new Int32Array(count);
    for (let q = 0; q < count; q++) {
      let bd = MAX_I32, bi = -1;
      // Workers own increasing id ranges, so ties keep the earlier worker's candidate.
      for (const part of parts) {
        if (part.dist[q] < bd) { bd = part.dist[q]; bi = part.index[q]; }
      }
      index[q] = bi;
      dist[q] = bd + qnorm[q];
    }
    return { index, dist, blank: blankDist };
  }

  destroy() {
    for (const worker of this.workers) worker.terminate();
  }
}
