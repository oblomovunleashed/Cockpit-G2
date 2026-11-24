import { AudioInputSource, type EvenAppBridge } from '@evenrealities/even_hub_sdk';

const SAMPLE_RATE = 16000;
const CHANNELS = 1;
const BITS_PER_SAMPLE = 16;
// Hard safety cap: 30 seconds of 16 kHz 16-bit mono PCM (~960 KB)
const MAX_PCM_BYTES = SAMPLE_RATE * CHANNELS * (BITS_PER_SAMPLE / 8) * 30;

/**
 * Collects raw 16 kHz, signed 16-bit little-endian mono PCM chunks from Even G2
 * (`audioEvent.audioPcm`) and wraps them in a standard 44-byte RIFF/WAV header
 * compatible with OpenRouter's /v1/audio/transcriptions endpoint.
 */
export class PcmAudioRecorder {
  private chunks: Uint8Array[] = [];
  private totalBytes = 0;
  private recording = false;

  get isRecording(): boolean {
    return this.recording;
  }

  async start(bridge: EvenAppBridge | null, source: AudioInputSource = AudioInputSource.Glasses): Promise<void> {
    this.chunks = [];
    this.totalBytes = 0;
    this.recording = true;
    if (bridge) {
      await bridge.audioControl(true, source);
    }
  }

  pushChunk(pcm: Uint8Array): void {
    if (!this.recording || pcm.byteLength === 0) return;
    if (this.totalBytes >= MAX_PCM_BYTES) return;
    const remaining = MAX_PCM_BYTES - this.totalBytes;
    const slice = pcm.byteLength <= remaining ? pcm : pcm.subarray(0, remaining);
    this.chunks.push(slice);
    this.totalBytes += slice.byteLength;
  }

  async stop(bridge: EvenAppBridge | null): Promise<Blob | null> {
    this.recording = false;
    if (bridge) {
      await bridge.audioControl(false);
    }
    if (this.totalBytes === 0) {
      return null;
    }
    const wavBytes = encodePcmToWav(this.chunks, this.totalBytes);
    this.chunks = [];
    this.totalBytes = 0;
    return new Blob([wavBytes], { type: 'audio/wav' });
  }
}

/**
 * Browser / companion WebView push-to-talk microphone capture.
 * Resamples browser AudioContext input down to 16 kHz 16-bit LE mono PCM
 * and encodes via the exact same `encodePcmToWav` pipeline as Even G2 audio.
 */
export class BrowserMicRecorder {
  private stream: MediaStream | null = null;
  private ctx: AudioContext | null = null;
  private processor: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private pcmChunks: Uint8Array[] = [];
  private totalBytes = 0;
  private recording = false;

  get isRecording(): boolean {
    return this.recording;
  }

  async start(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('Microphone capture is not supported in this browser/WebView.');
    }
    this.pcmChunks = [];
    this.totalBytes = 0;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    this.ctx = new AudioContext();
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.processor = this.ctx.createScriptProcessor(4096, 1, 1);

    const inputRate = this.ctx.sampleRate;
    this.processor.onaudioprocess = (evt: AudioProcessingEvent) => {
      if (!this.recording || this.totalBytes >= MAX_PCM_BYTES) return;
      const floatSamples = evt.inputBuffer.getChannelData(0);
      const pcm = downsampleToPcm16(floatSamples, inputRate, SAMPLE_RATE);
      const remaining = MAX_PCM_BYTES - this.totalBytes;
      const slice = pcm.byteLength <= remaining ? pcm : pcm.subarray(0, remaining);
      this.pcmChunks.push(slice);
      this.totalBytes += slice.byteLength;
    };

    this.source.connect(this.processor);
    this.processor.connect(this.ctx.destination);
    this.recording = true;
  }

  async stop(): Promise<Blob | null> {
    this.recording = false;
    this.processor?.disconnect();
    this.source?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    if (this.ctx) {
      await this.ctx.close();
    }
    this.processor = null;
    this.source = null;
    this.stream = null;
    this.ctx = null;

    if (this.totalBytes === 0) {
      return null;
    }
    const wavBytes = encodePcmToWav(this.pcmChunks, this.totalBytes);
    this.pcmChunks = [];
    this.totalBytes = 0;
    return new Blob([wavBytes], { type: 'audio/wav' });
  }
}

export function downsampleToPcm16(
  input: Float32Array,
  inputRate: number,
  targetRate: number,
): Uint8Array {
  const ratio = inputRate / targetRate;
  const outputLength = Math.max(1, Math.floor(input.length / ratio));
  const buffer = new ArrayBuffer(outputLength * 2);
  const view = new DataView(buffer);

  for (let i = 0; i < outputLength; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio) || start + 1);
    let sum = 0;
    let count = 0;
    for (let j = start; j < end; j += 1) {
      sum += input[j] ?? 0;
      count += 1;
    }
    const sample = Math.max(-1, Math.min(1, count > 0 ? sum / count : 0));
    const int16 = sample < 0 ? Math.round(sample * 0x8000) : Math.round(sample * 0x7fff);
    view.setInt16(i * 2, int16, true);
  }
  return new Uint8Array(buffer);
}

export function encodePcmToWav(chunks: Uint8Array[], pcmByteLength: number): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + pcmByteLength);
  const view = new DataView(buffer);

  writeAscii(view, 0, 'RIFF');
  view.setUint32(4, 36 + pcmByteLength, true);
  writeAscii(view, 8, 'WAVE');
  writeAscii(view, 12, 'fmt ');
  view.setUint32(16, 16, true); // PCM subchunk size
  view.setUint16(20, 1, true); // AudioFormat = PCM
  view.setUint16(22, CHANNELS, true);
  view.setUint32(24, SAMPLE_RATE, true);
  view.setUint32(28, (SAMPLE_RATE * CHANNELS * BITS_PER_SAMPLE) / 8, true);
  view.setUint16(32, (CHANNELS * BITS_PER_SAMPLE) / 8, true);
  view.setUint16(34, BITS_PER_SAMPLE, true);
  writeAscii(view, 36, 'data');
  view.setUint32(40, pcmByteLength, true);

  const output = new Uint8Array(buffer, 44);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return buffer;
}

function writeAscii(view: DataView, offset: number, text: string): void {
  for (let i = 0; i < text.length; i += 1) {
    view.setUint8(offset + i, text.charCodeAt(i));
  }
}
