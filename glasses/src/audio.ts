import { AudioInputSource, type EvenAppBridge } from '@evenrealities/even_hub_sdk';

const SAMPLE_RATE = 16000;
const CHANNELS = 1;
const BITS_PER_SAMPLE = 16;

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
    this.chunks.push(pcm);
    this.totalBytes += pcm.byteLength;
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
