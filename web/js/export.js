// Render every frame with the composer and encode an MP4 with the original audio.
import {
  AudioSampleSink, AudioSampleSource, BufferTarget, CanvasSource, EncodedAudioPacketSource,
  EncodedPacketSink, Mp4OutputFormat, Output, StreamTarget, VideoSampleSink, canEncodeAudio, canEncodeVideo,
} from '../vendor/mediabunny.min.mjs';

export const CODECS = { avc: 'H.264', hevc: 'H.265', vp9: 'VP9', av1: 'AV1' };

export async function pickCodec(preference, width, height, fps, bitrate) {
  const order = preference === 'auto' ? ['avc', 'hevc', 'vp9', 'av1'] : [preference];
  for (const codec of order) {
    if (await canEncodeVideo(codec, { width, height, bitrate, frameRate: fps }).catch(() => false)) return codec;
  }
  return null;
}

// Pulls items from an async iterator and adds those that start before `until` seconds.
function feeder(iterator, add) {
  let head = null, done = false;
  return async (until) => {
    for (;;) {
      if (!head && !done) {
        const next = await iterator.next();
        if (next.done) done = true; else head = next.value;
      }
      if (!head || head.at >= until) return;
      await add(head);
      head = null;
    }
  };
}

async function audioTrack(output, format, source, end) {
  const track = source.audio;
  if (!track) return null;
  const codec = track.codec;
  if (codec && format.getSupportedAudioCodecs().includes(codec)) {
    // Copy compressed packets unchanged.
    const target = new EncodedAudioPacketSource(codec);
    output.addAudioTrack(target);
    const decoderConfig = await track.getDecoderConfig();
    const packets = new EncodedPacketSink(track).packets();
    let first = true;
    const iterator = (async function* () {
      for await (const packet of packets) {
        const at = packet.timestamp - source.start;
        if (at >= end) break;
        if (at + packet.duration <= 0) continue;
        yield { at, packet: at < 0 ? packet.clone({ timestamp: 0 }) : packet.clone({ timestamp: at }) };
      }
    })();
    return { source: target, codec, feed: feeder(iterator, async ({ packet }) => {
      await target.add(packet, first ? { decoderConfig } : undefined);
      first = false;
    }) };
  }
  if (!(await track.canDecode())) return null;
  const encode = (await canEncodeAudio('aac')) ? 'aac' : (await canEncodeAudio('opus')) ? 'opus' : null;
  if (!encode) return null;
  const target = new AudioSampleSource({ codec: encode, bitrate: 192000 });
  output.addAudioTrack(target);
  const samples = new AudioSampleSink(track).samples(source.start, source.start + end);
  const iterator = (async function* () {
    for await (const sample of samples) {
      sample.setTimestamp(Math.max(0, sample.timestamp - source.start));
      yield { at: sample.timestamp, sample };
    }
  })();
  return { source: target, codec: encode, reencoded: true, feed: feeder(iterator, async ({ sample }) => {
    await target.add(sample);
    sample.close();
  }) };
}

export async function exportVideo({ source, composer, glyphsFor, fps, frameCount, time, codec, bitrate,
  includeAudio = true, writable, signal, onProgress }) {
  const format = new Mp4OutputFormat({ fastStart: writable ? false : 'in-memory' });
  const target = writable ? new StreamTarget(writable, { chunked: true }) : new BufferTarget();
  const output = new Output({ format, target });
  const canvas = new OffscreenCanvas(composer.width, composer.height);
  const ctx = canvas.getContext('2d', { alpha: false });
  const video = new CanvasSource(canvas, { codec, bitrate, keyFrameInterval: 2 });
  output.addVideoTrack(video, { frameRate: fps });
  const end = frameCount / fps;
  const audio = includeAudio ? await audioTrack(output, format, source, end) : null;
  await output.start();
  const sink = composer.showOriginal ? new VideoSampleSink(source.video) : null;
  const originals = sink
    ? sink.samplesAtTimestamps((function* () { for (let i = 0; i < frameCount; i++) yield time(i); })())[Symbol.asyncIterator]()
    : null;
  const started = performance.now();
  let last = 0;
  try {
    for (let i = 0; i < frameCount; i++) {
      if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
      const sample = originals ? (await originals.next()).value : null;
      composer.draw(ctx, i, glyphsFor(i), sample ? (c, x, y, w, h) => sample.draw(c, x, y, w, h) : null);
      sample?.close();
      // Keep audio slightly ahead of video so the muxer can interleave.
      if (audio) await audio.feed((i + 1) / fps + 0.5);
      await video.add(i / fps, 1 / fps);
      const now = performance.now();
      if (now - last > 150 || i === frameCount - 1) {
        last = now;
        onProgress?.({ frame: i + 1, count: frameCount, elapsed: (now - started) / 1000 });
      }
    }
    if (audio) await audio.feed(Infinity);
    await output.finalize();
  } catch (error) {
    await output.cancel().catch(() => {});
    await originals?.return?.();
    throw error;
  }
  return { buffer: writable ? null : target.buffer, audio: audio ? { codec: audio.codec, reencoded: !!audio.reencoded } : null };
}
