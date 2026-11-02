import { VideoSampleSink } from '../vendor/mediabunny.min.mjs';
import { analyze, frameTimes, matchFrame, openVideo, resolveCells } from './analyze.js';
import {
  BLANK, blankGlyph, candidateMask, dataFiles, deleteStoredData, downloadFiles, ensureGlyphs, loadCatalog, loadDictionary,
  loadMeta, missingFiles, openStore,
} from './dictionary.js';
import { CODECS, exportVideo, pickCodec } from './export.js';
import { applyLanguage, formatNumber, getLanguage, t } from './i18n.js';
import { CpuMatcher, GpuMatcher } from './matcher.js';
import { Composer, hex } from './render.js';

const $ = (id) => document.getElementById(id);
const DATA = new URL('data/', document.baseURI);
const MAX_COLUMNS = 64, MAX_ROWS = 48;
const BLOCKS_KEY = 'unicode-frames-blocks';

const state = {
  meta: null, store: null, dataReady: false, download: null,
  source: null, catalogPromise: null, catalog: null, dictPromise: null, dict: null, matcher: null,
  selected: null, mask: null,
  result: null, cells: null, settingsKey: '',
  composer: null, frame: 0, playing: false, abort: null,
  previewSink: null, showToken: 0,
  previewImage: null, previewKey: '', previewToken: 0, frameToken: 0, previewTimer: 0, matcherPromise: null,
};

const number = (n) => formatNumber(n);
const clock = (s) => {
  s = Math.max(0, s);
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
};
const bytes = (n) => n >= 1e9 ? `${(n / 1e9).toFixed(2)} GB` : `${(n / 1e6).toFixed(1)} MB`;

function setProgress(id, value, max, detail) {
  const row = $(id);
  const bar = row.querySelector('progress');
  bar.max = max || 1;
  bar.value = Math.min(value, max || 1);
  row.querySelector('.detail').textContent = detail ?? '';
}

function showError(id, error) {
  const el = $(id);
  if (!error) { el.hidden = true; return; }
  console.error(error);
  el.textContent = error.name === 'AbortError' ? t('aborted') : t('error', { message: error.message || error });
  el.hidden = false;
}

// ---------- dictionary ----------
function catalog() {
  if (!state.catalogPromise) {
    state.catalogPromise = loadCatalog(state.meta, state.store).then((loaded) => {
      state.catalog = loaded;
      state.selected = restoreBlocks(loaded);
      renderBlocks();
      updateGrid();
      return loaded;
    }).catch((error) => {
      state.catalogPromise = null;
      throw error;
    });
  }
  return state.catalogPromise;
}

function dictionary() {
  if (!state.dataReady) return Promise.reject(new Error(t('hint.noData')));
  if (!state.dictPromise) {
    state.dictPromise = catalog().then((loaded) => loadDictionary(loaded)).then((dict) => {
      state.dict = dict;
      setProgress('p-dict', 1, 1, t('dict.done', { n: number(dict.count) }));
      return dict;
    }).catch((error) => {
      state.dictPromise = null;
      throw error;
    });
  }
  return state.dictPromise;
}

// ---------- data download (asks first) ----------
function totalBytes(files) {
  return files.reduce((sum, f) => sum + f.bytes, 0);
}

async function initData() {
  try {
    state.meta = await loadMeta(DATA);
    state.store = await openStore(state.meta, DATA);
    const missing = await missingFiles(state.store, dataFiles(state.meta));
    if (!missing.length) { dataReady(true); return; }
    state.download = { files: missing };
    await renderConsent();
  } catch (error) {
    showError('consent-error', error);
    $('data-card').hidden = false;
    $('consent-actions').hidden = true;
  }
}

async function renderConsent() {
  const download = state.download;
  if (!download) return;
  const size = bytes(totalBytes(download.files));
  $('data-card').hidden = false;
  $('consent-accept').textContent = t('consent.accept', { size });
  let text = t('consent.size', { size });
  let note = download.declined ? t('consent.declined') : '';
  try {
    const { quota, usage } = await navigator.storage.estimate();
    if (quota) {
      text += ' · ' + t('consent.space', { free: bytes(quota - usage) });
      if (quota - usage < totalBytes(download.files) * 1.1) note = t('consent.lowSpace');
    }
  } catch { /* estimate unavailable */ }
  if (!state.store.cache) note = t('consent.noStorage');
  $('consent-size').textContent = text;
  $('consent-note').textContent = download.result?.persistent === false ? t('consent.noStorage') : note;
}

async function acceptDownload() {
  const download = state.download;
  if (!download || download.abort) return;
  showError('consent-error', null);
  download.abort = new AbortController();
  $('consent-accept').hidden = true;
  $('consent-decline').hidden = true;
  $('consent-cancel').hidden = false;
  $('p-download').hidden = false;
  const started = performance.now();
  try {
    // Ask the browser not to evict the data under storage pressure (may be declined silently).
    navigator.storage?.persist?.().catch(() => {});
    download.result = await downloadFiles(state.store, download.files, (loaded, total) => {
      const seconds = (performance.now() - started) / 1000;
      const rate = loaded / Math.max(seconds, 1e-3);
      setProgress('p-download', loaded, total, t('download.detail',
        { loaded: bytes(loaded), total: bytes(total), rate: bytes(rate), eta: clock((total - loaded) / rate) }));
    }, download.abort.signal);
    dataReady(download.result.persistent);
  } catch (error) {
    showError('consent-error', error);
    $('consent-accept').hidden = false;
    $('consent-decline').hidden = false;
  } finally {
    download.abort = null;
    $('consent-cancel').hidden = true;
  }
}

function dataReady(persistent) {
  state.dataReady = true;
  // A copy kept only in memory is gone next visit: say so instead of hiding the card.
  $('data-card').hidden = persistent;
  if (!persistent) {
    $('consent-actions').hidden = true;
    $('consent-note').textContent = t('consent.noStorage');
  }
  renderStoredData();
  catalog().catch((error) => showError('analyze-error', error));
  if (state.source) dictionary().catch((error) => showError('analyze-error', error));
  updateGrid();
}

function renderStoredData() {
  const show = state.dataReady && !!state.store?.cache && !state.store.memory.size;
  $('stored-data').hidden = !show;
  if (show) $('stored-data-text').textContent = t('data.stored', { size: bytes(totalBytes(dataFiles(state.meta))) });
}

// ---------- Unicode blocks ----------
const PRESETS = {
  all: () => true,
  ascii: (name) => name === 'Basic Latin',
  latin: (name) => /Latin/.test(name),
  kana: (name) => /^(Hiragana|Katakana|Katakana Phonetic Extensions|Kana Supplement|Kana Extended-[AB]|Small Kana Extension)$/.test(name),
  cjk: (name) => /^(CJK Unified Ideographs|CJK Compatibility Ideographs|CJK Radicals Supplement|Kangxi Radicals)/.test(name),
  shapes: (name) => /^(Box Drawing|Block Elements|Geometric Shapes|Geometric Shapes Extended|Symbols for Legacy Computing|Symbols for Legacy Computing Supplement)$/.test(name),
  braille: (name) => name === 'Braille Patterns',
  emoji: (name) => /^(Emoticons|Miscellaneous Symbols and Pictographs|Supplemental Symbols and Pictographs|Symbols and Pictographs Extended-A|Transport and Map Symbols|Miscellaneous Symbols|Dingbats)$/.test(name),
};

function usableBlocks(loaded = state.catalog) {
  return loaded.meta.blocks.map(([start, end, name], index) => ({ index, start, end, name, count: loaded.blockCounts[index] }))
    .filter((block) => block.count > 0);
}

function presetBlocks(preset) {
  return new Set(usableBlocks().filter((block) => PRESETS[preset](block.name)).map((block) => block.index));
}

function restoreBlocks(loaded) {
  // Stored by block start code point, which stays valid when the data is rebuilt.
  try {
    const saved = JSON.parse(localStorage.getItem(BLOCKS_KEY));
    if (Array.isArray(saved)) {
      const starts = new Set(saved);
      return new Set(usableBlocks(loaded).filter((block) => starts.has(block.start)).map((block) => block.index));
    }
  } catch { /* storage unavailable or invalid */ }
  return new Set(usableBlocks(loaded).map((block) => block.index));
}

function saveBlocks() {
  try {
    const all = usableBlocks().every((block) => state.selected.has(block.index));
    if (all) localStorage.removeItem(BLOCKS_KEY);
    else localStorage.setItem(BLOCKS_KEY, JSON.stringify(usableBlocks().filter((b) => state.selected.has(b.index)).map((b) => b.start)));
  } catch { /* storage unavailable */ }
}

function currentMask() {
  if (!state.catalog) return null;
  const key = [...state.selected].sort((a, b) => a - b).join(',');
  if (state.mask?.key !== key) state.mask = { ...candidateMask(state.catalog, state.selected), key };
  return state.mask;
}

function shownBlocks() {
  const query = $('block-search').value.trim().toLowerCase().replace(/^u\+/, '');
  if (!query) return usableBlocks();
  const cp = /^[0-9a-f]{1,6}$/.test(query) ? parseInt(query, 16) : -1;
  return usableBlocks().filter((block) => block.name.toLowerCase().includes(query) || (cp >= block.start && cp <= block.end));
}

function renderBlocks() {
  const list = $('block-list');
  if (!state.catalog) {
    $('blocks-summary').textContent = state.dataReady ? t('blocks.loading') : t('hint.noData');
    return;
  }
  const shown = shownBlocks();
  if (!shown.length) {
    const empty = document.createElement('p');
    empty.className = 'block-empty';
    empty.textContent = t('blocks.none');
    list.replaceChildren(empty);
  } else {
    list.replaceChildren(...shown.map((block) => {
      const row = document.createElement('label');
      row.className = 'block-row';
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.dataset.block = block.index;
      box.checked = state.selected.has(block.index);
      const name = document.createElement('span'); name.className = 'name'; name.textContent = block.name;
      const range = document.createElement('span'); range.className = 'range'; range.textContent = `${hex(block.start)}–${hex(block.end)}`;
      const count = document.createElement('span'); count.className = 'count'; count.textContent = number(block.count);
      row.append(box, name, range, count);
      return row;
    }));
  }
  renderBlockSummary();
}

function renderBlockSummary() {
  if (!state.catalog) return;
  const usable = usableBlocks();
  const mask = currentMask();
  $('blocks-summary').textContent = t('blocks.summary', { blocks: number(usable.filter((b) => state.selected.has(b.index)).length),
    total: number(usable.length), glyphs: number(mask.count) });
  for (const button of $('block-presets').querySelectorAll('button')) {
    const preset = presetBlocks(button.dataset.preset);
    button.classList.toggle('active', preset.size === state.selected.size && [...preset].every((b) => state.selected.has(b)));
  }
}

function blocksChanged() {
  saveBlocks();
  renderBlockSummary();
  updateGrid();
}

// ---------- step 1: video ----------
async function selectFile(file) {
  if (!file) return;
  stopPlayback();
  showError('video-error', null);
  $('video-facts').hidden = true;
  try {
    const source = await openVideo(file);
    state.source = source;
    state.previewSink = new VideoSampleSink(source.video);
    state.previewImage = null;
    state.previewKey = '';
    renderFacts();
    $('video-facts').hidden = false;
    $('include-audio-wrap').hidden = !source.audio;
    $('preview-position').disabled = false;
    if (state.dataReady) dictionary().catch((error) => showError('analyze-error', error));
    await loadPreviewFrame();
  } catch (error) {
    state.source = null;
    showError('video-error', error);
    updateGrid();
  }
}

function renderFacts() {
  const source = state.source;
  if (!source) return;
  const facts = [
    [t('fact.file'), source.file.name],
    [t('fact.resolution'), `${source.width} × ${source.height}`],
    [t('fact.duration'), clock(source.duration)],
    [t('fact.fps'), `${source.sourceFps.toFixed(3)} fps`],
    [t('fact.video'), source.video.codec ?? t('unknown')],
    [t('fact.audio'), source.audio ? (source.audio.codec ?? t('present')) : t('none')],
  ];
  $('video-facts').replaceChildren(...facts.map(([k, v]) => {
    const div = document.createElement('div');
    const dt = document.createElement('dt'); dt.textContent = k;
    const dd = document.createElement('dd'); dd.textContent = v;
    div.append(dt, dd);
    return div;
  }));
}

// ---------- step 2: grid, characters and preview ----------
function gridSettings() {
  const clamp = (v, max) => Math.max(1, Math.min(max, Math.round(Number(v) || 1)));
  const columns = clamp($('columns').value, MAX_COLUMNS), rows = clamp($('rows').value, MAX_ROWS);
  const source = state.source;
  const fps = $('fps').value === 'source' ? (source?.sourceFps || 30) : Number($('fps').value);
  return { columns, rows, fps };
}

function settingsKey() {
  const { columns, rows, fps } = gridSettings();
  return `${columns}x${rows}@${fps}|${currentMask()?.key ?? ''}`;
}

function updateGrid() {
  const { columns, rows, fps } = gridSettings();
  const cells = columns * rows;
  $('cell-count').textContent = number(cells);
  for (const button of $('presets').querySelectorAll('button')) {
    button.classList.toggle('active', button.dataset.grid === `${columns}x${rows}`);
  }
  drawOriginal();
  const source = state.source;
  if (source) {
    const frames = frameTimes(source, fps).count;
    const gpu = 'gpu' in navigator;
    $('estimate').textContent = t('estimate', { frames: number(frames), cells: number(cells), total: number(frames * cells) })
      + (gpu ? '' : '\n' + t('estimate.cpu'));
  } else {
    $('estimate').textContent = '';
  }
  const mask = currentMask();
  const noBlocks = mask && mask.count === 0;
  $('analyze').disabled = !source || !!state.abort || noBlocks || !mask || !state.dataReady;
  $('analyze-hint').textContent = !state.dataReady ? t('hint.noData')
    : !source ? t('hint.noVideo')
    : noBlocks ? t('hint.noBlocks')
    : state.result && settingsKey() !== state.settingsKey ? t('hint.changed')
    : state.result ? t('hint.done') : '';
  schedulePreview();
}

function previewCanvasSize() {
  const source = state.source;
  const aspect = source ? source.width / source.height : 4 / 3;
  return { width: 640, height: Math.round(640 / aspect) };
}

function drawOriginal() {
  const { columns, rows } = gridSettings();
  const canvas = $('grid-canvas');
  const { width, height } = previewCanvasSize();
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, width, height);
  if (state.previewImage) ctx.drawImage(state.previewImage, 0, 0, width, height);
  ctx.strokeStyle = 'rgba(245, 215, 110, 0.75)';
  ctx.lineWidth = columns * rows > 300 ? 0.5 : 1;
  ctx.beginPath();
  for (let c = 1; c < columns; c++) { const x = Math.round(c * width / columns) + 0.5; ctx.moveTo(x, 0); ctx.lineTo(x, height); }
  for (let r = 1; r < rows; r++) { const y = Math.round(r * height / rows) + 0.5; ctx.moveTo(0, y); ctx.lineTo(width, y); }
  ctx.stroke();
}

function previewTime() {
  const source = state.source;
  const position = Number($('preview-position').value) / 1000;
  // Stay just inside the last frame.
  return source.start + Math.min(position * source.duration, Math.max(0, source.duration - 0.001));
}

async function loadPreviewFrame() {
  const source = state.source;
  if (!source) return;
  const time = previewTime();
  $('preview-time').textContent = clock(time - source.start);
  const token = ++state.frameToken;
  const sample = await state.previewSink.getSample(time).catch(() => null);
  if (token !== state.frameToken || source !== state.source) { sample?.close(); return; }
  if (sample) {
    // Keep a moderate-resolution copy; matching rescales it to the analysis grid.
    const width = Math.min(1280, source.width);
    const canvas = new OffscreenCanvas(width, Math.round(width * source.height / source.width));
    sample.draw(canvas.getContext('2d'), 0, 0, canvas.width, canvas.height);
    sample.close();
    state.previewImage = canvas;
  }
  updateGrid();
}

function schedulePreview() {
  clearTimeout(state.previewTimer);
  state.previewTimer = setTimeout(runPreview, 200);
}

function setPreviewNote(text) {
  $('grid-result-note').textContent = text;
}

async function runPreview() {
  const source = state.source;
  const canvas = $('grid-result');
  const { height } = previewCanvasSize();
  if (!source || !state.previewImage) {
    ({ width: canvas.width, height: canvas.height } = previewCanvasSize());
    const { width } = canvas;
    state.previewKey = '';
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, width, height);
    state.previewGlyphs = 0;
    $('grid-result-info').textContent = '';
    setPreviewNote(t('preview.waiting'));
    return;
  }
  const mask = currentMask();
  const { columns, rows } = gridSettings();
  const useBlank = $('use-blank').checked;
  const key = `${columns}x${rows}|${mask?.key}|${useBlank}|${$('preview-position').value}|${source.file.name}`;
  if (key === state.previewKey) return;
  if (!state.dataReady) { setPreviewNote(t('hint.noData')); return; }
  if (state.abort) { setPreviewNote(t('preview.busy')); return; }
  if (!mask || mask.count === 0) {
    setPreviewNote(mask ? t('hint.noBlocks') : t('preview.loading'));
    return;
  }
  const token = ++state.previewToken;
  try {
    if (!state.dict) setPreviewNote(t('preview.loading'));
    const dict = await dictionary();
    const matcher = await getMatcher(dict);
    if (token !== state.previewToken) return;
    setPreviewNote(t('preview.matching'));
    const result = await matchFrame({ dict, matcher, columns, rows, image: state.previewImage, mask: mask.words });
    const ids = Array.from(result.index, (id, i) => useBlank && result.blank[i] < result.dist[i] ? BLANK : id);
    await ensureGlyphs(dict, ids);
    if (token !== state.previewToken) return;
    const blank = blankGlyph(dict);
    const composer = new Composer({ dict, columns, rows, aspect: source.width / source.height, height,
      showOriginal: false, showInfo: false, frameCount: 1 });
    canvas.width = composer.width;
    canvas.height = composer.height;
    composer.draw(canvas.getContext('2d'), 0, ids.map((id) => id === BLANK ? blank : dict.glyphs.get(id)), null);
    state.previewGlyphs = new Set(ids).size;
    renderPreviewInfo();
    setPreviewNote('');
    state.previewKey = key;
  } catch (error) {
    if (token === state.previewToken) setPreviewNote(t('error', { message: error.message || error }));
  }
}

function renderPreviewInfo() {
  $('grid-result-info').textContent = state.previewKey || state.previewGlyphs
    ? t('preview.info', { glyphs: number(state.previewGlyphs) }) : '';
}

async function getMatcher(dict) {
  if (state.matcher && !state.matcher.lost) return state.matcher;
  if (!state.matcherPromise) {
    state.matcherPromise = (async () => {
      const gpu = await GpuMatcher.create(dict).catch((error) => { console.warn(error); return null; });
      state.matcher = gpu || CpuMatcher.create(dict);
      state.matcherPromise = null;
      return state.matcher;
    })();
  }
  return state.matcherPromise;
}

async function runAnalysis() {
  const source = state.source;
  if (!source) return;
  stopPlayback();
  const { columns, rows, fps } = gridSettings();
  const mask = currentMask();
  const key = settingsKey();
  const abort = new AbortController();
  state.abort = abort;
  showError('analyze-error', null);
  $('analysis-progress').hidden = false;
  $('cancel-analyze').hidden = false;
  setProgress('p-frames', 0, 1, '');
  setProgress('p-glyphs', 0, 1, '');
  updateGrid();
  try {
    const dict = await dictionary();
    const matcher = await getMatcher(dict);
    setProgress('p-frames', 0, 1, t('frames.preparing', { label: matcher.label }));
    const result = await analyze({
      source, dict, matcher, columns, rows, fps, mask: mask.words, signal: abort.signal,
      onProgress: ({ frame, count, distinct, elapsed }) => {
        const rate = frame / Math.max(elapsed, 1e-3);
        const eta = rate > 0 ? (count - frame) / rate : 0;
        setProgress('p-frames', frame, count, t('frames.detail',
          { frame: number(frame), count: number(count), distinct: number(distinct), rate: rate.toFixed(1), eta: clock(eta) }));
      },
    });
    state.result = result;
    state.settingsKey = key;
    await refreshCells();
    state.frame = Math.min(state.frame, result.frameCount - 1);
    $('scrub').max = String(result.frameCount - 1);
    $('scrub').value = String(state.frame);
    $('step-preview').hidden = false;
    $('step-export').hidden = false;
    rebuildComposer();
    renderSummary();
    updateExportSize();
  } catch (error) {
    showError('analyze-error', error);
  } finally {
    state.abort = null;
    $('cancel-analyze').hidden = true;
    updateGrid();
  }
}

function renderSummary() {
  const { result, cells, matcher } = state;
  if (!result || !cells) return;
  const used = new Set(cells).size;
  $('summary').textContent = t('summary', { label: matcher.label, frames: number(result.frameCount), cells: number(result.cells),
    distinct: number(result.distinct), used: number(used), seconds: result.seconds.toFixed(1) });
}

async function refreshCells() {
  const result = state.result;
  const cells = resolveCells(result, $('use-blank').checked, BLANK);
  const ids = [...new Set(cells)].filter((id) => id >= 0);
  setProgress('p-glyphs', 0, ids.length, '');
  await ensureGlyphs(state.dict, ids, (done, total) => setProgress('p-glyphs', done, total, t('glyphs.detail', { done: number(done), total: number(total) })));
  setProgress('p-glyphs', 1, 1, t('dict.done', { n: number(ids.length) }));
  state.cells = cells;
}

function glyphsFor(frame) {
  const { cells, dict, result } = state;
  const blank = blankGlyph(dict);
  const out = new Array(result.cells);
  const base = frame * result.cells;
  for (let i = 0; i < result.cells; i++) {
    const id = cells[base + i];
    out[i] = id === BLANK ? blank : dict.glyphs.get(id);
  }
  return out;
}

// ---------- step 3: preview ----------
function composerFor(height) {
  const { result, source, dict } = state;
  return new Composer({
    dict, columns: result.columns, rows: result.rows, aspect: source.width / source.height, height,
    showOriginal: $('show-original').checked, showInfo: $('show-info').checked, frameCount: result.frameCount,
  });
}

function rebuildComposer() {
  if (!state.result) return;
  state.composer = composerFor(720);
  const canvas = $('preview');
  canvas.width = state.composer.width;
  canvas.height = state.composer.height;
  showFrame(state.frame);
  updateExportSize();
}

async function showFrame(frame) {
  const token = ++state.showToken;
  const { composer, result } = state;
  if (!composer) return;
  let sample = null;
  if (composer.showOriginal) {
    sample = await state.previewSink.getSample(result.time(frame)).catch(() => null);
    if (token !== state.showToken) { sample?.close(); return; }
  }
  drawPreview(frame, sample);
  sample?.close();
}

function drawPreview(frame, sample) {
  const { composer, result } = state;
  composer.draw($('preview').getContext('2d'), frame, glyphsFor(frame),
    sample ? (c, x, y, w, h) => sample.draw(c, x, y, w, h) : null);
  $('time').textContent = `${clock(frame / result.fps)} / ${clock(result.frameCount / result.fps)}`;
  if (!state.playing) $('scrub').value = String(frame);
  state.frame = frame;
}

let audioElement = null, audioUrl = null;
function audioFor(file) {
  if (audioElement && audioElement.dataset.name === file.name + file.size) return audioElement;
  if (audioUrl) URL.revokeObjectURL(audioUrl);
  audioUrl = URL.createObjectURL(file);
  audioElement = new Audio(audioUrl);
  audioElement.dataset.name = file.name + file.size;
  audioElement.preload = 'auto';
  return audioElement;
}

async function startPlayback() {
  const { result, source } = state;
  if (!result || state.playing) return;
  state.playing = true;
  $('play').textContent = '❚❚';
  $('play').setAttribute('aria-label', t('pause'));
  const startFrame = state.frame >= result.frameCount - 1 ? 0 : state.frame;
  const startTime = startFrame / result.fps;
  const audio = source.audio ? audioFor(source.file) : null;
  let audioOk = false;
  if (audio) {
    try {
      audio.currentTime = source.start + startTime;
      await audio.play();
      audioOk = true;
    } catch { audioOk = false; }
  }
  const t0 = performance.now();
  const now = () => audioOk ? audio.currentTime - source.start : startTime + (performance.now() - t0) / 1000;
  // Original frames stream in decode order; the newest one not later than the clock is shown.
  let latest = null;
  const generation = ++state.showToken;
  if (state.composer.showOriginal) {
    (async () => {
      for await (const sample of state.previewSink.samples(source.start + startTime)) {
        while (state.playing && generation === state.showToken && sample.timestamp - source.start > now() + 0.02) {
          await new Promise(requestAnimationFrame);
        }
        if (!state.playing || generation !== state.showToken) { sample.close(); break; }
        latest?.close();
        latest = sample;
      }
    })().catch(() => {});
  }
  let shown = -1;
  const tick = () => {
    if (!state.playing || generation !== state.showToken) { latest?.close(); latest = null; return; }
    const frame = Math.floor(now() * result.fps);
    if (frame >= result.frameCount) { stopPlayback(); showFrame(result.frameCount - 1); return; }
    if (frame !== shown && frame >= 0) {
      shown = frame;
      drawPreview(frame, latest);
      $('scrub').value = String(frame);
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function stopPlayback() {
  if (!state.playing) return;
  state.playing = false;
  state.showToken++;
  audioElement?.pause();
  $('play').textContent = '▶';
  $('play').setAttribute('aria-label', t('play'));
}

// ---------- step 4: export ----------
function exportPlan() {
  const height = Number($('height').value);
  const composer = composerFor(height);
  const bitrate = Math.round(composer.width * composer.height * state.result.fps * Number($('quality').value));
  return { composer, bitrate };
}

function updateExportSize() {
  if (!state.result) return;
  const { composer, bitrate } = exportPlan();
  const seconds = state.result.frameCount / state.result.fps;
  $('export-size').textContent = t('exportSize', { width: composer.width, height: composer.height,
    mbps: (bitrate / 1e6).toFixed(1), size: bytes(bitrate * seconds / 8) });
}

function baseName() {
  const name = state.source.file.name.replace(/\.[^.]+$/, '');
  return `${name}-unicode-${state.result.columns}x${state.result.rows}`;
}

function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

async function runExport() {
  if (!state.result) return;
  stopPlayback();
  showError('export-error', null);
  $('export-done').hidden = true;
  const direct = !$('save-direct-wrap').hidden && $('save-direct').checked;
  let writable = null, handle = null;
  try {
    if (direct) {
      // Must be the first await of the click handler (user activation).
      handle = await window.showSaveFilePicker({ suggestedName: `${baseName()}.mp4`,
        types: [{ description: 'MP4', accept: { 'video/mp4': ['.mp4'] } }] });
    }
  } catch (error) {
    if (error.name === 'AbortError') return;
    showError('export-error', error);
    return;
  }
  const abort = new AbortController();
  state.abort = abort;
  $('export').disabled = true;
  $('cancel-export').hidden = false;
  $('p-export').hidden = false;
  setProgress('p-export', 0, 1, t('export.preparing'));
  try {
    const { composer, bitrate } = exportPlan();
    const result = state.result;
    const includeAudio = $('include-audio').checked;
    const codec = await pickCodec($('codec').value, composer.width, composer.height, result.fps, bitrate);
    if (!codec) throw new Error(t('err.encode'));
    if (handle) writable = await handle.createWritable();
    const output = await exportVideo({
      source: state.source, composer, glyphsFor, fps: result.fps, frameCount: result.frameCount, time: result.time,
      codec, bitrate, includeAudio, writable, signal: abort.signal,
      onProgress: ({ frame, count, elapsed }) => {
        const rate = frame / Math.max(elapsed, 1e-3);
        setProgress('p-export', frame, count, t('export.detail',
          { frame: number(frame), count: number(count), rate: rate.toFixed(1), eta: clock((count - frame) / rate) }));
      },
    });
    const audioNote = output.audio
      ? t('audio.codec', { codec: output.audio.reencoded ? t('audio.reencoded', { codec: output.audio.codec }) : output.audio.codec })
      : !state.source.audio ? t('audio.none') : !includeAudio ? t('audio.off') : t('audio.unsupported');
    if (output.buffer) download(new Blob([output.buffer], { type: 'video/mp4' }), `${baseName()}.mp4`);
    $('export-done').textContent = t('export.done', { codec: CODECS[codec], width: composer.width, height: composer.height, audio: audioNote })
      + (output.buffer ? ` · ${bytes(output.buffer.byteLength)}` : ` · ${handle.name}`);
    $('export-done').hidden = false;
  } catch (error) {
    if (writable) await writable.abort().catch(() => {});
    showError('export-error', error);
  } finally {
    state.abort = null;
    $('export').disabled = false;
    $('cancel-export').hidden = true;
  }
}

function downloadText() {
  const { result, cells, dict } = state;
  const lines = [];
  for (let f = 0; f < result.frameCount; f++) {
    let line = '';
    for (let i = 0; i < result.cells; i++) {
      const id = cells[f * result.cells + i];
      line += String.fromCodePoint(id === BLANK ? dict.meta.blank.codepoint : dict.glyphs.get(id).codepoint);
    }
    lines.push(line);
  }
  download(new Blob([lines.join('\n') + '\n'], { type: 'text/plain;charset=utf-8' }), `${baseName()}.txt`);
}

// ---------- wiring ----------
$('file').addEventListener('change', (e) => selectFile(e.target.files[0]));
const drop = $('drop');
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); selectFile(e.dataTransfer.files[0]); });
$('presets').addEventListener('click', (e) => {
  const grid = e.target.closest('button')?.dataset.grid;
  if (!grid) return;
  const [c, r] = grid.split('x');
  $('columns').value = c;
  $('rows').value = r;
  updateGrid();
});
for (const id of ['columns', 'rows', 'fps']) $(id).addEventListener('input', updateGrid);
for (const id of ['columns', 'rows']) $(id).addEventListener('change', () => {
  const { columns, rows } = gridSettings();
  $('columns').value = columns;
  $('rows').value = rows;
  updateGrid();
});
$('block-presets').addEventListener('click', (e) => {
  const preset = e.target.closest('button')?.dataset.preset;
  if (!preset || !state.catalog) return;
  state.selected = presetBlocks(preset);
  renderBlocks();
  blocksChanged();
});
$('block-list').addEventListener('change', (e) => {
  const index = Number(e.target.dataset.block);
  if (Number.isNaN(index)) return;
  if (e.target.checked) state.selected.add(index); else state.selected.delete(index);
  blocksChanged();
});
$('block-search').addEventListener('input', renderBlocks);
for (const [id, select] of [['blocks-select-shown', true], ['blocks-clear-shown', false]]) {
  $(id).addEventListener('click', () => {
    if (!state.catalog) return;
    for (const block of shownBlocks()) {
      if (select) state.selected.add(block.index); else state.selected.delete(block.index);
    }
    renderBlocks();
    blocksChanged();
  });
}
$('preview-position').addEventListener('input', () => {
  if (state.source) $('preview-time').textContent = clock(previewTime() - state.source.start);
});
$('preview-position').addEventListener('change', loadPreviewFrame);
$('analyze').addEventListener('click', runAnalysis);
$('cancel-analyze').addEventListener('click', () => state.abort?.abort());
$('cancel-export').addEventListener('click', () => state.abort?.abort());
$('show-original').addEventListener('change', () => { stopPlayback(); rebuildComposer(); });
$('show-info').addEventListener('change', () => { stopPlayback(); rebuildComposer(); });
$('use-blank').addEventListener('change', async () => {
  schedulePreview();
  if (!state.result) return;
  stopPlayback();
  try {
    await refreshCells();
    renderSummary();
    showFrame(state.frame);
  } catch (error) { showError('analyze-error', error); }
});
$('scrub').addEventListener('input', (e) => { stopPlayback(); showFrame(Number(e.target.value)); });
$('play').addEventListener('click', () => state.playing ? stopPlayback() : startPlayback());
for (const id of ['height', 'quality']) $(id).addEventListener('change', updateExportSize);
$('export').addEventListener('click', runExport);
$('download-text').addEventListener('click', downloadText);
$('consent-accept').addEventListener('click', acceptDownload);
$('consent-decline').addEventListener('click', () => {
  if (!state.download) return;
  state.download.declined = true;
  renderConsent();
});
$('consent-cancel').addEventListener('click', () => state.download?.abort?.abort());
$('delete-data').addEventListener('click', async () => {
  if (!window.confirm(t('data.confirmDelete'))) return;
  await deleteStoredData();
  location.reload();
});
$('lang').addEventListener('click', () => {
  applyLanguage(getLanguage() === 'en' ? 'ja' : 'en');
  $('lang').lang = getLanguage() === 'en' ? 'ja' : 'en';
  $('play').setAttribute('aria-label', t(state.playing ? 'pause' : 'play'));
  renderFacts();
  renderConsent();
  renderStoredData();
  renderBlocks();
  updateGrid();
  renderSummary();
  updateExportSize();
  if (state.previewKey) renderPreviewInfo(); else runPreview();
});
if ('showSaveFilePicker' in window) $('save-direct-wrap').hidden = false;
applyLanguage();
$('lang').lang = getLanguage() === 'en' ? 'ja' : 'en';
renderBlocks();
updateGrid();
initData();

// ?video=URL loads a video without the file picker (handy for testing and links).
const linked = new URLSearchParams(location.search).get('video');
if (linked) {
  fetch(new URL(linked, location.href)).then((r) => {
    if (!r.ok) throw new Error(`${linked}: HTTP ${r.status}`);
    return r.blob();
  }).then((blob) => selectFile(new File([blob], decodeURIComponent(linked.split('/').pop()), { type: blob.type })))
    .catch((error) => showError('video-error', error));
}
