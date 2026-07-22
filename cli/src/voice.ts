import { execSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';

const RECORD_SECONDS = 15;
const SAMPLE_RATE = 16000;

/** Проверяет доступность ffmpeg для записи. */
export function canRecord(): boolean {
  try {
    execSync('ffmpeg -version', { stdio: 'ignore', timeout: 3000 });
    return true;
  } catch { return false; }
}

/** Проверяет доступность mlx-whisper. */
export function canTranscribe(): boolean {
  try {
    execSync('uvx --offline --from mlx-whisper==0.4.3 mlx_whisper --help', {
      stdio: 'ignore', timeout: 10000,
    });
    return true;
  } catch { return false; }
}

/** Записывает аудио с микрофона (macOS avfoundation). */
function recordAudio(outputPath: string, seconds: number): boolean {
  try {
    // macOS: avfoundation device :0 = default mic
    const result = spawnSync('ffmpeg', [
      '-y', '-f', 'avfoundation',
      '-i', ':0',
      '-t', String(seconds),
      '-ar', String(SAMPLE_RATE),
      '-ac', '1',
      '-sample_fmt', 's16le',
      outputPath,
    ], { stdio: 'ignore', timeout: (seconds + 5) * 1000 });
    return result.status === 0 && fs.existsSync(outputPath) && fs.statSync(outputPath).size > 1000;
  } catch { return false; }
}

/** Транскрибирует аудио через mlx-whisper (Apple Silicon GPU). */
function transcribe(audioPath: string): string | null {
  try {
    const result = spawnSync('uvx', [
      '--offline', '--from', 'mlx-whisper==0.4.3',
      'mlx_whisper',
      '--model', 'mlx-community/whisper-large-v3-turbo',
      '--language', 'ru',
      '--output-format', 'txt',
      '--verbose', 'False',
      audioPath,
    ], { encoding: 'utf-8', timeout: 120000 });

    if (result.status === 0 && result.stdout.trim()) {
      return result.stdout.trim();
    }
    return null;
  } catch { return null; }
}

/** Полный цикл: запись → транскрипция → текст. */
export function voiceToText(seconds = RECORD_SECONDS): string | null {
  const tmpWav = path.join(os.tmpdir(), `ha-voice-${Date.now()}.wav`);

  try {
    // Запись
    process.stderr.write(`\x1b[33m🎙 запись ${seconds}с… говорите\x1b[0m\n`);
    if (!recordAudio(tmpWav, seconds)) {
      process.stderr.write('\x1b[31m✗ запись не удалась (проверьте доступ к микрофону)\x1b[0m\n');
      return null;
    }

    const size = fs.statSync(tmpWav).size;
    process.stderr.write(`\x1b[32m✓ записано ${(size / 1024).toFixed(0)}KB, транскрибирую…\x1b[0m\n`);

    // Транскрипция
    const text = transcribe(tmpWav);
    if (text) {
      process.stderr.write(`\x1b[32m✓ распознано: ${text.slice(0, 80)}${text.length > 80 ? '…' : ''}\x1b[0m\n`);
    } else {
      process.stderr.write('\x1b[31m✗ транскрипция не удалась\x1b[0m\n');
    }
    return text;
  } finally {
    try { fs.unlinkSync(tmpWav); } catch { /* ignore */ }
  }
}