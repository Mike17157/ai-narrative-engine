import { copyFile, mkdir, stat } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const hostDirectory = resolve(scriptDirectory, '..');
const target = process.env.TAURI_TARGET_TRIPLE || 'x86_64-pc-windows-msvc';
const source = join(hostDirectory, 'dist', 'story-host.exe');
const destinationDirectory = resolve(hostDirectory, '..', 'frontend', 'src-tauri', 'binaries');
const destination = join(destinationDirectory, `story-host-${target}.exe`);

try {
  await stat(source);
} catch {
  throw new Error('Build the Story Host first: npm run build:sidecar');
}

await mkdir(destinationDirectory, { recursive: true });
await copyFile(source, destination);
console.log(`Staged Story Host sidecar for ${target}: ${destination}`);
