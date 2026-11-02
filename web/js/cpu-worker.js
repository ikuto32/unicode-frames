// One shard of the candidate table; exhaustive int8 dot products for the CPU fallback.
let features, norms, base, dims, mask = null;

self.onmessage = ({ data }) => {
  if (data.type === 'init') {
    ({ features, norms, base, dims } = data);
    return;
  }
  if (data.type === 'mask') {
    mask = data.mask;
    return;
  }
  const { queries, count } = data;
  const rows = norms.length;
  const index = new Int32Array(count), dist = new Int32Array(count);
  for (let q = 0; q < count; q++) {
    const qo = q * dims;
    let best = 0x7fffffff, bestIndex = -1;
    for (let c = 0; c < rows; c++) {
      const id = base + c;
      if (mask && !((mask[id >> 5] >>> (id & 31)) & 1)) continue;
      const co = c * dims;
      let dot = 0;
      for (let d = 0; d < dims; d += 8) {
        dot += queries[qo + d] * features[co + d] + queries[qo + d + 1] * features[co + d + 1]
          + queries[qo + d + 2] * features[co + d + 2] + queries[qo + d + 3] * features[co + d + 3]
          + queries[qo + d + 4] * features[co + d + 4] + queries[qo + d + 5] * features[co + d + 5]
          + queries[qo + d + 6] * features[co + d + 6] + queries[qo + d + 7] * features[co + d + 7];
      }
      const value = norms[c] - 2 * dot;
      if (value < best) { best = value; bestIndex = c; }
    }
    index[q] = bestIndex < 0 ? -1 : base + bestIndex;
    dist[q] = best;
  }
  self.postMessage({ index, dist }, [index.buffer, dist.buffer]);
};
