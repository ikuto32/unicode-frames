// Frame cell -> 32x24 uint8 feature, matching badapple_unicode.glyphs.feature():
// Pillow LANCZOS resize, then Pillow GaussianBlur (three extended box passes).
// Both are ported with Pillow's fixed-point rounding so features agree with Python.

const PRECISION_BITS = 22;

function sinc(x) {
  if (x === 0) return 1;
  x *= Math.PI;
  return Math.sin(x) / x;
}

function lanczos(x) {
  return x >= -3 && x < 3 ? sinc(x) * sinc(x / 3) : 0;
}

// Pillow precompute_coeffs + normalize_coeffs_8bpc for a full-width resize.
export function resampleCoefficients(inSize, outSize) {
  const scale = inSize / outSize;
  const filterScale = Math.max(scale, 1);
  const support = 3 * filterScale;
  const bounds = new Int32Array(outSize * 2);
  const kernels = [];
  for (let xx = 0; xx < outSize; xx++) {
    const center = (xx + 0.5) * scale;
    let xmin = Math.trunc(center - support + 0.5);
    if (xmin < 0) xmin = 0;
    let xmax = Math.trunc(center + support + 0.5);
    if (xmax > inSize) xmax = inSize;
    xmax -= xmin;
    const weights = new Float64Array(xmax);
    let total = 0;
    for (let x = 0; x < xmax; x++) {
      const w = lanczos((x + xmin - center + 0.5) / filterScale);
      weights[x] = w;
      total += w;
    }
    const fixed = new Int32Array(xmax);
    for (let x = 0; x < xmax; x++) {
      const k = total !== 0 ? weights[x] / total : 0;
      fixed[x] = k < 0 ? Math.trunc(-0.5 + k * (1 << PRECISION_BITS)) : Math.trunc(0.5 + k * (1 << PRECISION_BITS));
    }
    bounds[xx * 2] = xmin;
    bounds[xx * 2 + 1] = xmax;
    kernels.push(fixed);
  }
  return { bounds, kernels };
}

function clip8(value) {
  const v = Math.floor(value / (1 << PRECISION_BITS));
  return v < 0 ? 0 : v > 255 ? 255 : v;
}

// Pillow _gaussian_blur_radius, evaluated in float32 like the C code.
function boxRadius(radius, passes) {
  const f = Math.fround;
  const r = f(radius);
  const sigma2 = f(f(r * r) / passes);
  const L = f(Math.sqrt(12 * sigma2 + 1));
  const l = f(Math.floor((L - 1) / 2));
  let a = f(f(2 * l + 1) * f(f(l * f(l + 1)) - f(3 * sigma2)));
  a = f(a / f(6 * f(sigma2 - f(f(l + 1) * f(l + 1)))));
  return f(l + a);
}

// Pillow ImagingHorizontalBoxBlur + ImagingLineBoxBlur8 (main branch; 32/24-pixel lines take it).
function lineBoxBlur(input, output, length, floatRadius) {
  const lastx = length - 1;
  const radius = Math.trunc(floatRadius);
  const ww = Math.trunc(Math.fround((1 << 24) / Math.fround(floatRadius * 2 + 1)));
  const fw = Math.trunc(((1 << 24) - (radius * 2 + 1) * ww) / 2);
  const edgeA = Math.min(radius + 1, length);
  const edgeB = Math.max(length - radius - 1, 0);
  if (edgeA > edgeB) throw new Error('Blur radius too large for feature size');
  let acc = input[0] * (radius + 1);
  for (let x = 0; x < edgeA - 1; x++) acc += input[x];
  acc += input[lastx] * (radius - edgeA + 1);
  const save = (x, bulk) => { output[x] = Math.floor((bulk + (1 << 23)) / (1 << 24)); };
  for (let x = 0; x < edgeA; x++) {
    acc += input[x + radius] - input[0];
    save(x, acc * ww + (input[0] + input[x + radius + 1]) * fw);
  }
  for (let x = edgeA; x < edgeB; x++) {
    acc += input[x + radius] - input[x - radius - 1];
    save(x, acc * ww + (input[x - radius - 1] + input[x + radius + 1]) * fw);
  }
  for (let x = edgeB; x <= lastx; x++) {
    acc += input[lastx] - input[x - radius - 1];
    save(x, acc * ww + (input[x - radius - 1] + input[lastx]) * fw);
  }
}

export class CellFeaturizer {
  // cellWidth x cellHeight luma pixels per cell -> featureWidth x featureHeight.
  constructor(cellWidth, cellHeight, featureWidth = 32, featureHeight = 24, blur = 0.65) {
    this.cw = cellWidth;
    this.ch = cellHeight;
    this.fw = featureWidth;
    this.fh = featureHeight;
    this.horizontal = cellWidth === featureWidth ? null : resampleCoefficients(cellWidth, featureWidth);
    this.vertical = cellHeight === featureHeight ? null : resampleCoefficients(cellHeight, featureHeight);
    this.radius = blur > 0 ? boxRadius(blur, 3) : 0;
    this.cell = new Uint8Array(cellWidth * cellHeight);
    this.temp = new Uint8Array(featureWidth * cellHeight);
    this.small = new Uint8Array(featureWidth * featureHeight);
    this.lineIn = new Uint8Array(Math.max(featureWidth, featureHeight));
    this.lineOut = new Uint8Array(Math.max(featureWidth, featureHeight));
  }

  // luma: Uint8Array of a canvas `stride` pixels wide; writes 768 bytes at out[offset].
  feature(luma, stride, left, top, out, offset) {
    const { cw, ch, fw, fh } = this;
    const cell = this.cell;
    for (let y = 0; y < ch; y++) cell.set(luma.subarray((top + y) * stride + left, (top + y) * stride + left + cw), y * cw);
    // Horizontal pass first, rounding to 8 bits, as Pillow does.
    const temp = this.temp;
    if (this.horizontal) {
      const { bounds, kernels } = this.horizontal;
      for (let y = 0; y < ch; y++) {
        const row = y * cw;
        for (let x = 0; x < fw; x++) {
          const xmin = bounds[x * 2], count = bounds[x * 2 + 1], k = kernels[x];
          let ss = 1 << (PRECISION_BITS - 1);
          for (let i = 0; i < count; i++) ss += cell[row + xmin + i] * k[i];
          temp[y * fw + x] = clip8(ss);
        }
      }
    } else {
      temp.set(cell);
    }
    const small = this.small;
    if (this.vertical) {
      const { bounds, kernels } = this.vertical;
      for (let y = 0; y < fh; y++) {
        const ymin = bounds[y * 2], count = bounds[y * 2 + 1], k = kernels[y];
        for (let x = 0; x < fw; x++) {
          let ss = 1 << (PRECISION_BITS - 1);
          for (let i = 0; i < count; i++) ss += temp[(ymin + i) * fw + x] * k[i];
          small[y * fw + x] = clip8(ss);
        }
      }
    } else {
      small.set(temp.subarray(0, fw * fh));
    }
    if (this.radius > 0) {
      const lineIn = this.lineIn, lineOut = this.lineOut;
      for (let pass = 0; pass < 3; pass++) {
        for (let y = 0; y < fh; y++) {
          for (let x = 0; x < fw; x++) lineIn[x] = small[y * fw + x];
          lineBoxBlur(lineIn, lineOut, fw, this.radius);
          for (let x = 0; x < fw; x++) small[y * fw + x] = lineOut[x];
        }
      }
      for (let pass = 0; pass < 3; pass++) {
        for (let x = 0; x < fw; x++) {
          for (let y = 0; y < fh; y++) lineIn[y] = small[y * fw + x];
          lineBoxBlur(lineIn, lineOut, fh, this.radius);
          for (let y = 0; y < fh; y++) small[y * fw + x] = lineOut[y];
        }
      }
    }
    out.set(small, offset);
  }
}

// Pillow's RGB -> L conversion: L = R*299/1000 + G*587/1000 + B*114/1000 (fixed point).
export function rgbaToLuma(rgba, luma) {
  for (let i = 0, j = 0; j < luma.length; i += 4, j++) {
    luma[j] = (rgba[i] * 19595 + rgba[i + 1] * 38470 + rgba[i + 2] * 7471 + 0x8000) >> 16;
  }
  return luma;
}

// Analysis canvas: each cell is (32k x 24k) pixels. k=10 reproduces the CLI's
// 320x240 source for one cell; large grids use smaller k to bound the canvas.
export function analysisLayout(columns, rows, featureWidth = 32, featureHeight = 24) {
  const k = Math.max(1, Math.min(10, Math.floor(1280 / (columns * featureWidth)), Math.floor(960 / (rows * featureHeight))));
  return { k, cellWidth: featureWidth * k, cellHeight: featureHeight * k,
    width: columns * featureWidth * k, height: rows * featureHeight * k };
}
