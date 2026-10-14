/**
 * Audio helpers for live microphone monitoring: resample the microphone's float PCM (usually
 * 44.1 or 48 kHz) to 16 kHz mono, convert to 16-bit integers, cut fixed-length chunks and wrap
 * each chunk in a minimal WAV container (RIFF, PCM format 1, mono, 16 kHz, 16-bit).
 */

export const TARGET_RATE = 16000;

/** Streaming resampler (box-filter average for downsampling, linear interpolation for upsampling). */
export class Resampler {
  private carry = new Float32Array(0);
  private readonly ratio: number;
  constructor(readonly inRate: number, readonly outRate = TARGET_RATE) {
    this.ratio = inRate / outRate;
  }

  process(input: Float32Array): Float32Array {
    if (this.inRate === this.outRate) return input.slice();
    const buf = new Float32Array(this.carry.length + input.length);
    buf.set(this.carry);
    buf.set(input, this.carry.length);
    const r = this.ratio;
    const n = Math.floor((buf.length - 1) / r);
    const out = new Float32Array(Math.max(0, n));
    for (let i = 0; i < n; i++) {
      if (r >= 1) {
        const a = Math.floor(i * r);
        const b = Math.max(a + 1, Math.floor((i + 1) * r));
        let sum = 0;
        for (let k = a; k < b; k++) sum += buf[k] ?? 0;
        out[i] = sum / (b - a);
      } else {
        const x = i * r;
        const a = Math.floor(x);
        const f = x - a;
        out[i] = (buf[a] ?? 0) * (1 - f) + (buf[a + 1] ?? 0) * f;
      }
    }
    this.carry = buf.slice(Math.floor(n * r));
    return out;
  }
}

export function floatToInt16(f: Float32Array): Int16Array {
  const out = new Int16Array(f.length);
  for (let i = 0; i < f.length; i++) {
    const v = Math.max(-1, Math.min(1, f[i] ?? 0));
    out[i] = v < 0 ? Math.round(v * 0x8000) : Math.round(v * 0x7fff);
  }
  return out;
}

export function encodeWav(samples: Int16Array, sampleRate = TARGET_RATE): Uint8Array {
  const dataBytes = samples.length * 2;
  const buf = new ArrayBuffer(44 + dataBytes);
  const v = new DataView(buf);
  const ascii = (off: number, s: string) => {
    for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i));
  };
  ascii(0, "RIFF");
  v.setUint32(4, 36 + dataBytes, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, sampleRate, true);
  v.setUint32(28, sampleRate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  ascii(36, "data");
  v.setUint32(40, dataBytes, true);
  for (let i = 0; i < samples.length; i++) v.setInt16(44 + i * 2, samples[i] ?? 0, true);
  return new Uint8Array(buf);
}

export function rms(f: Float32Array): number {
  let s = 0;
  for (let i = 0; i < f.length; i++) s += (f[i] ?? 0) ** 2;
  return f.length ? Math.sqrt(s / f.length) : 0;
}

export interface Chunk {
  samples: Int16Array;
  /** Offset of the chunk's first sample, in ms of captured audio since monitoring started. */
  startMs: number;
}

/** Collects 16 kHz samples and emits fixed-length chunks (the last one may be shorter on flush). */
export class Chunker {
  private parts: Int16Array[] = [];
  private have = 0;
  private emitted = 0; // samples already emitted
  readonly size: number;
  constructor(chunkMs: number, readonly rate = TARGET_RATE) {
    this.size = Math.round((chunkMs / 1000) * rate);
  }

  push(s: Int16Array): Chunk[] {
    this.parts.push(s);
    this.have += s.length;
    const out: Chunk[] = [];
    while (this.have >= this.size) out.push(this.take(this.size));
    return out;
  }

  /** Remaining audio as a final chunk when it is at least `minMs` long. */
  flush(minMs = 300): Chunk | null {
    const minSamples = Math.round((minMs / 1000) * this.rate);
    if (this.have < minSamples) {
      this.clear();
      return null;
    }
    return this.take(this.have);
  }

  clear() {
    this.parts = [];
    this.have = 0;
  }

  private take(n: number): Chunk {
    const out = new Int16Array(n);
    let filled = 0;
    while (filled < n) {
      const p = this.parts[0];
      if (!p) break;
      const need = n - filled;
      if (p.length <= need) {
        out.set(p, filled);
        filled += p.length;
        this.parts.shift();
      } else {
        out.set(p.subarray(0, need), filled);
        this.parts[0] = p.subarray(need);
        filled += need;
      }
    }
    this.have -= n;
    const startMs = Math.round((this.emitted / this.rate) * 1000);
    this.emitted += n;
    return { samples: out, startMs };
  }
}
