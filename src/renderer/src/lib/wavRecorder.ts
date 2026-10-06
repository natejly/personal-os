/**
 * Microphone to a 16 kHz mono PCM16 WAV, for the composer's dictation. No dependency: the samples
 * are collected from a ScriptProcessor, averaged down to 16 kHz, and given a 44-byte RIFF header.
 */

export const WAV_RATE = 16000

/** Box-average `input` (at `inRate`) down to `outRate`. Upsampling is never needed: mics run at 16 kHz or more. */
export function downsample(input: Float32Array, inRate: number, outRate = WAV_RATE): Float32Array {
  if (inRate <= outRate) return input
  const ratio = inRate / outRate
  const out = new Float32Array(Math.floor(input.length / ratio))
  for (let i = 0; i < out.length; i++) {
    const a = Math.floor(i * ratio)
    const b = Math.min(input.length, Math.floor((i + 1) * ratio))
    let sum = 0
    for (let j = a; j < b; j++) sum += input[j]
    out[i] = b > a ? sum / (b - a) : 0
  }
  return out
}

/** A mono PCM16 WAV of `samples` (at `inRate`), resampled to 16 kHz. */
export function encodeWav(samples: Float32Array, inRate: number): ArrayBuffer {
  const pcm = downsample(samples, inRate)
  const buf = new ArrayBuffer(44 + pcm.length * 2)
  const v = new DataView(buf)
  const str = (at: number, s: string): void => { for (let i = 0; i < s.length; i++) v.setUint8(at + i, s.charCodeAt(i)) }
  str(0, 'RIFF'); v.setUint32(4, 36 + pcm.length * 2, true); str(8, 'WAVE')
  str(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true)
  v.setUint32(24, WAV_RATE, true); v.setUint32(28, WAV_RATE * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true)
  str(36, 'data'); v.setUint32(40, pcm.length * 2, true)
  for (let i = 0; i < pcm.length; i++) {
    const s = Math.max(-1, Math.min(1, pcm[i]))
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }
  return buf
}

/** Root-mean-square level of a block of samples, 0 to 1. */
export const rms = (b: Float32Array): number => {
  let sum = 0
  for (let i = 0; i < b.length; i++) sum += b[i] * b[i]
  return b.length ? Math.sqrt(sum / b.length) : 0
}

export interface WavRecording {
  /** Level of the newest audio block, for silence detection. */
  level: () => number
  /** Release the mic and return the clip. */
  stop: () => Promise<Blob>
  /** Release the mic and drop the audio. */
  cancel: () => void
}

/** Open the mic and start collecting. Rejects when the mic is denied or missing. */
export async function startWavRecording(): Promise<WavRecording> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } })
  const ctx = new AudioContext()
  const src = ctx.createMediaStreamSource(stream)
  // ponytail: ScriptProcessor is deprecated but needs no module file; an AudioWorklet if Chromium drops it.
  const proc = ctx.createScriptProcessor(4096, 1, 1)
  const chunks: Float32Array[] = []
  let last = 0
  proc.onaudioprocess = (e) => { const b = new Float32Array(e.inputBuffer.getChannelData(0)); last = rms(b); chunks.push(b) }
  src.connect(proc)
  proc.connect(ctx.destination)
  const release = (): void => {
    proc.disconnect(); src.disconnect()
    for (const t of stream.getTracks()) t.stop()
    void ctx.close()
  }
  return {
    level: () => last,
    stop: async () => {
      release()
      const all = new Float32Array(chunks.reduce((n, c) => n + c.length, 0))
      let at = 0
      for (const c of chunks) { all.set(c, at); at += c.length }
      return new Blob([encodeWav(all, ctx.sampleRate)], { type: 'audio/wav' })
    },
    cancel: release
  }
}
