// Composes one output frame: the glyph grid, plus an optional side panel with the
// original frame and the glyphs in use. Every label has a fixed position so
// changing names or code points never shift the layout.
import { unicodeBlock } from './dictionary.js';

const BACKGROUND = '#000000';
const PANEL = '#0c1018';
const YELLOW = '#f5d76e';
const TEAL = '#65d6c4';
const GRAY = '#919aa9';
const DIM = '#657187';
const MONO = '"Cascadia Mono", "SFMono-Regular", Consolas, "Noto Sans Mono", "DejaVu Sans Mono", monospace';
const MAX_CODEPOINT = 0x10ffff;

export function hex(cp) {
  return 'U+' + cp.toString(16).toUpperCase().padStart(4, '0');
}

export class Composer {
  constructor({ dict, columns, rows, aspect, height, showOriginal, showInfo, frameCount }) {
    this.dict = dict;
    this.columns = columns;
    this.rows = rows;
    this.cells = columns * rows;
    this.showOriginal = showOriginal;
    this.showInfo = showInfo;
    this.frameCount = frameCount;
    const panel = showOriginal || showInfo;
    let h = height;
    let mainWidth = Math.round(h * aspect);
    let panelWidth = panel ? Math.round(h * 0.56) : 0;
    // Stay within common encoder limits.
    const limit = 3840;
    if (mainWidth + panelWidth > limit) {
      const scale = limit / (mainWidth + panelWidth);
      h = Math.floor(h * scale);
      mainWidth = Math.round(h * aspect);
      panelWidth = panel ? Math.round(h * 0.56) : 0;
    }
    this.height = h + (h % 2);
    const width = mainWidth + panelWidth;
    this.width = width + (width % 2);
    this.main = { x: 0, y: 0, w: mainWidth, h: this.height };
    this.panel = panel ? { x: mainWidth, y: 0, w: this.width - mainWidth, h: this.height } : null;
    this.u = this.height / 1080;
  }

  font(size, weight = '') {
    return `${weight} ${Math.max(1, Math.round(size * this.u))}px ${MONO}`;
  }

  // glyphs: one glyph object per cell (row-major).
  // original: null, or a function (ctx, x, y, w, h) that draws the source frame.
  draw(ctx, frameIndex, glyphs, original) {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = BACKGROUND;
    ctx.fillRect(0, 0, this.width, this.height);
    const { x: mx, y: my, w: mw, h: mh } = this.main;
    ctx.fillStyle = '#ffffff';
    for (let r = 0; r < this.rows; r++) {
      const y0 = my + Math.round(r * mh / this.rows), y1 = my + Math.round((r + 1) * mh / this.rows);
      for (let c = 0; c < this.columns; c++) {
        const x0 = mx + Math.round(c * mw / this.columns), x1 = mx + Math.round((c + 1) * mw / this.columns);
        drawGlyph(ctx, glyphs[r * this.columns + c], x0, y0, x1 - x0, y1 - y0);
      }
    }
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    if (this.panel) this.drawPanel(ctx, frameIndex, glyphs, original);
  }

  drawPanel(ctx, frameIndex, glyphs, original) {
    const p = this.panel, u = this.u;
    const pad = Math.round(28 * u);
    const inner = p.w - pad * 2;
    ctx.fillStyle = PANEL;
    ctx.fillRect(p.x, p.y, p.w, p.h);
    ctx.textBaseline = 'alphabetic';
    ctx.textAlign = 'left';
    let y = pad;
    ctx.fillStyle = GRAY;
    ctx.font = this.font(18);
    const label = this.cells === 1 ? 'ONE UNICODE' : `UNICODE GRID ${this.columns}×${this.rows}`;
    ctx.fillText(label, p.x + pad, y + Math.round(16 * u));
    y += Math.round(44 * u);
    if (this.showOriginal) {
      ctx.fillStyle = GRAY;
      ctx.font = this.font(15);
      ctx.fillText('ORIGINAL', p.x + pad, y + Math.round(14 * u));
      y += Math.round(24 * u);
      const aspect = this.main.w / this.main.h;
      let w = inner, h = Math.round(w / aspect);
      const maxH = Math.round(this.height * (this.showInfo ? 0.3 : 0.6));
      if (h > maxH) { h = maxH; w = Math.round(h * aspect); }
      const x = p.x + pad;
      ctx.fillStyle = DIM;
      ctx.fillRect(x - 2, y - 2, w + 4, h + 4);
      ctx.fillStyle = '#000';
      ctx.fillRect(x, y, w, h);
      if (original) original(ctx, x, y, w, h);
      y += h + Math.round(36 * u);
    }
    const footer = p.h - pad;
    if (this.showInfo) {
      if (this.cells === 1) this.drawSingle(ctx, glyphs[0], p.x + pad, y, inner);
      else this.drawLegend(ctx, glyphs, p.x + pad, y, inner, footer - Math.round(34 * u) - y);
    }
    ctx.fillStyle = GRAY;
    ctx.font = this.font(15);
    ctx.textAlign = 'right';
    const digits = String(this.frameCount).length;
    ctx.fillText(`frame ${String(frameIndex + 1).padStart(digits, '0')}/${this.frameCount}`, p.x + p.w - pad, footer);
    ctx.textAlign = 'left';
    ctx.fillText(`${this.cells} char${this.cells > 1 ? 's' : ''}/frame`, p.x + pad, footer);
  }

  drawSingle(ctx, glyph, x, y, width) {
    const u = this.u;
    const block = unicodeBlock(this.dict, glyph.codepoint);
    ctx.fillStyle = YELLOW;
    ctx.font = this.font(46, '600');
    ctx.fillText(hex(glyph.codepoint), x, y + Math.round(44 * u));
    y += Math.round(76 * u);
    ctx.fillStyle = TEAL;
    ctx.font = this.font(19);
    fitText(ctx, block ? block.name : 'No_Block', x, y, width);
    y += Math.round(24 * u);
    ctx.fillStyle = GRAY;
    ctx.font = this.font(15);
    ctx.fillText(block ? `${hex(block.start)}..${hex(block.end)}` : '', x, y);
    y += Math.round(42 * u);
    ctx.fillStyle = '#ffffff';
    ctx.font = this.font(22);
    // Up to three wrapped lines; the space they reserve is fixed.
    const lines = wrap(ctx, glyph.name, width, 3);
    lines.forEach((line, i) => ctx.fillText(line, x, y + i * Math.round(28 * u)));
    y += Math.round(3 * 28 * u + 8 * u);
    ctx.fillStyle = GRAY;
    ctx.font = this.font(15);
    fitText(ctx, glyph.blank ? 'whitespace' : glyph.font, x, y, width);
    y += Math.round(52 * u);
    this.axis(ctx, glyph.codepoint, 0, MAX_CODEPOINT, x, y, width, 'UNICODE', block);
    y += Math.round(62 * u);
    const start = block ? block.start : glyph.codepoint, end = block ? block.end : glyph.codepoint;
    this.axis(ctx, glyph.codepoint, start, end, x, y, width, 'BLOCK', null);
  }

  axis(ctx, cp, start, end, x, y, width, label, block) {
    const u = this.u;
    const pos = (v) => start === end ? x + width / 2 : x + width * (v - start) / (end - start);
    ctx.fillStyle = GRAY;
    ctx.font = this.font(12);
    ctx.fillText(label, x, y - Math.round(14 * u));
    ctx.strokeStyle = DIM;
    ctx.lineWidth = Math.max(1, 2 * u);
    line(ctx, x, y, x + width, y);
    if (block) {
      ctx.strokeStyle = TEAL;
      ctx.lineWidth = Math.max(2, 4 * u);
      line(ctx, pos(block.start), y, Math.max(pos(block.end), pos(block.start) + 1), y);
    }
    ctx.strokeStyle = DIM;
    ctx.lineWidth = Math.max(1, u);
    const ticks = label === 'UNICODE'
      ? Array.from({ length: 17 }, (_, i) => i * 0x10000)
      : Array.from({ length: 17 }, (_, i) => Math.round(start + (end - start) * i / 16));
    for (const tick of ticks) line(ctx, pos(tick), y - 3 * u, pos(tick), y + 3 * u);
    ctx.fillStyle = GRAY;
    ctx.font = this.font(11);
    ctx.fillText(hex(start), x, y + Math.round(20 * u));
    ctx.textAlign = 'right';
    ctx.fillText(hex(end), x + width, y + Math.round(20 * u));
    ctx.textAlign = 'left';
    const m = pos(cp);
    ctx.strokeStyle = YELLOW;
    ctx.lineWidth = Math.max(1, 2 * u);
    line(ctx, m, y - 6 * u, m, y + 5 * u);
    ctx.fillStyle = YELLOW;
    ctx.beginPath();
    ctx.moveTo(m, y - 6 * u);
    ctx.lineTo(m - 4 * u, y - 12 * u);
    ctx.lineTo(m + 4 * u, y - 12 * u);
    ctx.closePath();
    ctx.fill();
  }

  drawLegend(ctx, glyphs, x, y, width, height) {
    const u = this.u;
    const rowHeight = Math.round(24 * u);
    const rowsFit = Math.max(1, Math.floor(height / rowHeight));
    const digits = String(this.cells).length;
    // One row per cell: the fewest columns whose rows still fit, rows as tall as the space allows.
    for (let columns = 1; columns <= 12; columns++) {
      const perColumn = Math.ceil(this.cells / columns);
      const row = Math.min(46 * u, height / perColumn);
      if (row < 15 * u || width / columns < this.rowWidth(ctx, row, digits)) continue;
      ctx.fillStyle = GRAY;
      ctx.font = this.font(13);
      ctx.fillText('CELL / GLYPH / CODE POINT', x, y - Math.round(10 * u));
      for (let i = 0; i < this.cells; i++) {
        const cx = x + Math.floor(i / perColumn) * (width / columns);
        const cy = y + (i % perColumn) * row;
        this.legendRow(ctx, glyphs[i], String(i + 1).padStart(digits, '0'), cx, cy, row);
      }
      return;
    }
    // Too many cells for one row each: distinct glyphs of this frame by frequency.
    const counts = new Map();
    for (const g of glyphs) {
      const entry = counts.get(g.id);
      if (entry) entry.count++; else counts.set(g.id, { glyph: g, count: 1 });
    }
    const sorted = [...counts.values()].sort((a, b) => b.count - a.count || a.glyph.codepoint - b.glyph.codepoint);
    ctx.fillStyle = GRAY;
    ctx.font = this.font(13);
    ctx.fillText(`${sorted.length} DISTINCT / ${this.cells} CELLS`, x, y - Math.round(10 * u));
    const countDigits = String(this.cells).length;
    const entry = this.rowWidth(ctx, rowHeight, countDigits + 1);
    const listColumns = Math.max(1, Math.floor(width / entry));
    const shown = Math.min(sorted.length, rowsFit * listColumns);
    for (let i = 0; i < shown; i++) {
      const cx = x + Math.floor(i / rowsFit) * (width / listColumns);
      const cy = y + (i % rowsFit) * rowHeight;
      this.legendRow(ctx, sorted[i].glyph, `×${String(sorted[i].count).padStart(countDigits, ' ')}`, cx, cy, rowHeight, true);
    }
  }

  // Row layout: [label] [glyph box] U+XXXXX, all proportional to the row height.
  rowMetrics(ctx, row, labelChars) {
    const size = Math.max(8, row * 0.48);
    ctx.font = `${size}px ${MONO}`;
    const label = ctx.measureText('0'.repeat(labelChars)).width;
    const code = ctx.measureText('U+00000').width;
    const gap = row * 0.25;
    const box = Math.round(row * 0.8);
    return { size, label, code, gap, box };
  }

  rowWidth(ctx, row, labelChars) {
    const m = this.rowMetrics(ctx, row, labelChars);
    return m.label + m.box + m.code + m.gap * 4;
  }

  legendRow(ctx, glyph, label, x, y, row, labelAfter = false) {
    const m = this.rowMetrics(ctx, row, label.length);
    const baseline = y + row / 2 + m.size * 0.35;
    let cx = x;
    ctx.fillStyle = GRAY;
    if (!labelAfter) {
      ctx.fillText(label, cx, baseline);
      cx += m.label + m.gap;
    }
    const top = y + (row - m.box) / 2;
    ctx.fillStyle = '#1b2230';
    ctx.fillRect(cx, top, m.box, m.box);
    ctx.fillStyle = '#ffffff';
    if (!glyph.blank) {
      // Legend glyphs keep their own aspect ratio inside a square box.
      const inset = m.box * 0.08, box = m.box - inset * 2;
      const a = glyph.aspect || 1;
      const w = a >= 1 ? box : box * a, h = a >= 1 ? box / a : box;
      drawGlyph(ctx, glyph, cx + inset + (box - w) / 2, top + inset + (box - h) / 2, w, h);
    }
    cx += m.box + m.gap;
    ctx.font = `${m.size}px ${MONO}`;
    ctx.fillStyle = '#d5dae3';
    ctx.fillText(hex(glyph.codepoint), cx, baseline);
    if (labelAfter) {
      ctx.fillStyle = GRAY;
      ctx.fillText(label, cx + m.code + m.gap, baseline);
    }
  }
}

export function drawGlyph(ctx, glyph, x, y, w, h) {
  if (!glyph || glyph.blank || w <= 0 || h <= 0) return;
  if (glyph.image) {
    ctx.drawImage(glyph.image, x, y, w, h);
    return;
  }
  if (!glyph.path) return;
  ctx.setTransform(w / glyph.width, 0, 0, h / glyph.height, x, y);
  ctx.fill(glyph.path, 'nonzero');
  ctx.setTransform(1, 0, 0, 1, 0, 0);
}

function line(ctx, x0, y0, x1, y1) {
  ctx.beginPath();
  ctx.moveTo(x0, y0);
  ctx.lineTo(x1, y1);
  ctx.stroke();
}

function fitText(ctx, text, x, y, width) {
  if (ctx.measureText(text).width > width) {
    while (text.length > 1 && ctx.measureText(text + '...').width > width) text = text.slice(0, -1);
    text += '...';
  }
  ctx.fillText(text, x, y);
}

function wrap(ctx, text, width, maxLines) {
  const words = text.split(' ');
  const lines = [];
  let current = '';
  for (const word of words) {
    const candidate = current ? current + ' ' + word : word;
    if (ctx.measureText(candidate).width <= width || !current) current = candidate;
    else { lines.push(current); current = word; }
  }
  if (current) lines.push(current);
  if (lines.length > maxLines) {
    const kept = lines.slice(0, maxLines);
    let last = kept[maxLines - 1] + ' ' + lines.slice(maxLines).join(' ');
    while (last.length > 1 && ctx.measureText(last + '...').width > width) last = last.slice(0, -1);
    kept[maxLines - 1] = last + '...';
    return kept;
  }
  return lines;
}
