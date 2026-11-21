// One-shot generator for PWA icons (pure Node, no image deps).
// Usage: node scripts/gen-icons.mjs
import { deflateSync } from 'node:zlib';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const publicDir = join(root, 'public');

// ---- minimal PNG encoder ----
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(buf) {
  let c = 0xffffffff;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}

function encodePng(size, pixels /* RGBA rows */) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  const raw = Buffer.alloc(size * (size * 4 + 1));
  for (let y = 0; y < size; y++) {
    raw[y * (size * 4 + 1)] = 0; // filter: none
    pixels.copy(raw, y * (size * 4 + 1) + 1, y * size * 4, (y + 1) * size * 4);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

// ---- icon drawing: brand bg, white circular badge, 3 list lines ----
const BG = [47, 111, 79, 255];
const FG = [255, 255, 255, 255];
const LINE = [47, 111, 79, 255];

function draw(size, badgeScale) {
  const px = Buffer.alloc(size * size * 4);
  const set = (x, y, c) => {
    const i = (y * size + x) * 4;
    px[i] = c[0];
    px[i + 1] = c[1];
    px[i + 2] = c[2];
    px[i + 3] = c[3];
  };

  const r = size / 2;
  const badgeR = r * badgeScale; // maskable keeps content in the inner safe zone
  const lineHalf = size * 0.013;
  const lineCenters = [0.44, 0.5, 0.56].map((f) => f * size);
  const lineWidths = [0.22 * size, 0.22 * size, 0.13 * size];

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = x - r + 0.5;
      const dy = y - r + 0.5;
      set(x, y, BG);
      if (Math.hypot(dx, dy) <= badgeR) {
        set(x, y, FG);
        for (let li = 0; li < 3; li++) {
          if (
            Math.abs(y + 0.5 - lineCenters[li]) <= lineHalf &&
            Math.abs(dx) <= lineWidths[li] / 2
          ) {
            set(x, y, LINE);
          }
        }
      }
    }
  }
  return encodePng(size, px);
}

mkdirSync(publicDir, { recursive: true });
writeFileSync(join(publicDir, 'pwa-192x192.png'), draw(192, 0.58));
writeFileSync(join(publicDir, 'pwa-512x512.png'), draw(512, 0.58));
writeFileSync(join(publicDir, 'maskable-icon-512x512.png'), draw(512, 0.42));
console.log('icons written to', publicDir);
