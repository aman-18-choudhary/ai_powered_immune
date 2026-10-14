import { describe, expect, it } from "vitest";
import { Chunker, Resampler, encodeWav, floatToInt16, rms } from "./wav";

describe("WAV encoder", () => {
  it("writes a 44-byte RIFF/PCM mono 16 kHz 16-bit header and little-endian samples", () => {
    const wav = encodeWav(new Int16Array([0, 1, -1, 32767, -32768]));
    const v = new DataView(wav.buffer);
    const s = (o: number, n: number) => String.fromCharCode(...wav.slice(o, o + n));
    expect(s(0, 4)).toBe("RIFF");
    expect(v.getUint32(4, true)).toBe(36 + 10);
    expect(s(8, 4)).toBe("WAVE");
    expect(s(12, 4)).toBe("fmt ");
    expect(v.getUint16(20, true)).toBe(1);
    expect(v.getUint16(22, true)).toBe(1);
    expect(v.getUint32(24, true)).toBe(16000);
    expect(v.getUint32(28, true)).toBe(32000);
    expect(v.getUint16(34, true)).toBe(16);
    expect(s(36, 4)).toBe("data");
    expect(v.getUint32(40, true)).toBe(10);
    expect(wav.length).toBe(54);
    expect(v.getInt16(44 + 6, true)).toBe(32767);
    expect(v.getInt16(44 + 8, true)).toBe(-32768);
  });

  it("clips floats to int16", () => {
    expect(Array.from(floatToInt16(new Float32Array([0, 1, -1, 2, -2, 0.5])))).toEqual([0, 32767, -32768, 32767, -32768, 16384]);
  });
});

describe("Resampler", () => {
  it("downsamples 48 kHz to 16 kHz (1/3 the samples) across calls without drift", () => {
    const r = new Resampler(48000);
    let total = 0;
    for (let i = 0; i < 10; i++) total += r.process(new Float32Array(4800).fill(0.25)).length;
    expect(Math.abs(total - 16000)).toBeLessThanOrEqual(1);
  });

  it("averages (box filter) and keeps a 1 kHz tone at the right frequency", () => {
    const inRate = 44100;
    const src = new Float32Array(inRate).map((_, i) => Math.sin((2 * Math.PI * 1000 * i) / inRate));
    const out = new Resampler(inRate).process(src);
    expect(Math.abs(out.length - 16000)).toBeLessThanOrEqual(1);
    let crossings = 0;
    for (let i = 1; i < out.length; i++) if ((out[i - 1] ?? 0) < 0 && (out[i] ?? 0) >= 0) crossings++;
    expect(Math.abs(crossings - 1000)).toBeLessThanOrEqual(2);
    expect(rms(out)).toBeGreaterThan(0.6);
  });

  it("passes 16 kHz through unchanged", () => {
    const x = new Float32Array([0.1, 0.2, 0.3]);
    expect(Array.from(new Resampler(16000).process(x))).toEqual(Array.from(x));
  });
});

describe("Chunker", () => {
  it("emits fixed 4 s chunks with start offsets and flushes the tail", () => {
    const c = new Chunker(4000);
    const got = [...c.push(new Int16Array(50000)), ...c.push(new Int16Array(30000))];
    expect(got.map((x) => x.samples.length)).toEqual([64000]);
    expect(got[0]?.startMs).toBe(0);
    const tail = c.flush();
    expect(tail?.samples.length).toBe(16000);
    expect(tail?.startMs).toBe(4000);
  });

  it("drops a flush shorter than 300 ms", () => {
    const c = new Chunker(4000);
    c.push(new Int16Array(1000));
    expect(c.flush()).toBeNull();
  });
});
