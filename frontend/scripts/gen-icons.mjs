// One-shot generator for PWA icons (pure Node, no image deps).
// Usage: node scripts/gen-icons.mjs
// Design: dark tile + light-green wireframe SHOPPING BASKET
// (matches public/favicon.svg — keep the two in sync).
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
  const crcBuf = Buffer.alloc(4);
  crcBuf.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crcBuf]);
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

// ---- icon drawing: dark tile, light-green wireframe shopping basket ----
const BG = [18, 24, 21, 255]; // #121815
const FG = [138, 214, 160, 255]; // #8ad6a0
const REF = 512; // geometry defined on a 512 grid, scaled to any size
const W = 26; // stroke width at REF

// stroke segments of the basket, on the REF grid: [x1, y1, x2, y2]
const SEGS = [
  [112, 224, 400, 224], // rim
  [112, 224, 168, 400], // left side
  [400, 224, 344, 400], // right side
  [168, 400, 344, 400], // bottom
  [208, 250, 208, 380], // rib left
  [256, 250, 256, 380], // rib middle
  [304, 250, 304, 380], // rib right
];
// handle: arc of the circle centred (256,192) r=70, visible only above the rim
const HCX = 256;
const HCY = 192;
const HR = 70;

function segDist(px, py, seg) {
  const [x1, y1, x2, y2] = seg;
  const vx = x2 - x1;
  const vy = y2 - y1;
  const len2 = vx * vx + vy * vy;
  let t = ((px - x1) * vx + (py - y1) * vy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * vx), py - (y1 + t * vy));
}

function draw(size, safe) {
  const px = Buffer.alloc(size * size * 4);
  const set = (x, y, c) => {
    const i = (y * size + x) * 4;
    px[i] = c[0];
    px[i + 1] = c[1];
    px[i + 2] = c[2];
    px[i + 3] = c[3];
  };
  const s = size / REF;
  const half = (W * s) / 2;
  const cx = size / 2;
  const cy = size / 2;
  const k = safe ?? 1; // maskable safe-zone scale

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      set(x, y, BG);
      const refX = (x - cx) / k + REF / 2;
      const refY = (y - cy) / k + REF / 2;
      let hit = false;
      for (const seg of SEGS) {
        if (segDist(refX, refY, seg) <= half) {
          hit = true;
          break;
        }
      }
      if (!hit) {
        const dh = Math.hypot(refX - HCX, refY - HCY);
        if (Math.abs(dh - HR) <= half && refY < 224) hit = true;
      }
      if (hit) set(x, y, FG);
    }
  }
  return encodePng(size, px);
}

mkdirSync(publicDir, { recursive: true });
writeFileSync(join(publicDir, 'pwa-192x192.png'), draw(192, 0.58));
writeFileSync(join(publicDir, 'pwa-512x512.png'), draw(512, 0.58));
writeFileSync(join(publicDir, 'maskable-icon-512x512.png'), draw(512, 0.42));
console.log('icons written to', publicDir);
