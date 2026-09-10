/**
 * Generate the extension icons as PNGs, with no image library.
 *
 * Placeholder art, but real files: Chrome refuses to load an extension whose
 * declared icons are missing, and a designed icon is a launch task, not a
 * phase-one task. Replace assets/*.png whenever the real artwork exists.
 *
 * Run: node tools/make-icons.mjs
 */

import { deflateSync } from 'node:zlib';
import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const OUT = join(dirname(fileURLToPath(import.meta.url)), '..', 'extension', 'assets');

const BG = [47, 91, 215];      // the accent blue used across the UI
const FG = [255, 255, 255];

/** Three sound-wave bars, as fractions of the canvas: [x, halfHeight]. */
const BARS = [[0.32, 0.14], [0.5, 0.26], [0.68, 0.18]];
const BAR_W = 0.085;
const RADIUS = 0.22; // rounded-square corner, as a fraction of the side

/** @returns {Buffer} raw RGBA pixels, row-major */
function render(size) {
  const px = Buffer.alloc(size * size * 4);
  const r = RADIUS * size;

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const i = (y * size + x) * 4;

      // Rounded-square mask, with the corner test only near the corners.
      const cx = Math.min(x, size - 1 - x);
      const cy = Math.min(y, size - 1 - y);
      let inside = true;
      if (cx < r && cy < r) {
        inside = (r - cx) ** 2 + (r - cy) ** 2 <= r * r;
      }
      if (!inside) continue; // leave fully transparent

      let colour = BG;
      for (const [bx, bh] of BARS) {
        const left = (bx - BAR_W / 2) * size;
        const right = (bx + BAR_W / 2) * size;
        const top = (0.5 - bh) * size;
        const bottom = (0.5 + bh) * size;
        if (x >= left && x <= right && y >= top && y <= bottom) { colour = FG; break; }
      }

      px[i] = colour[0];
      px[i + 1] = colour[1];
      px[i + 2] = colour[2];
      px[i + 3] = 255;
    }
  }
  return px;
}

const CRC_TABLE = (() => {
  const t = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c;
  }
  return t;
})();

function crc32(buf) {
  let c = -1;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}

function png(size, rgba) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8;   // 8 bits per channel
  ihdr[9] = 6;   // RGBA
  // 10-12: deflate, adaptive filtering, no interlace — all zero already.

  // One filter byte (0 = none) in front of each scanline.
  const raw = Buffer.alloc(size * (size * 4 + 1));
  for (let y = 0; y < size; y++) {
    raw[y * (size * 4 + 1)] = 0;
    rgba.copy(raw, y * (size * 4 + 1) + 1, y * size * 4, (y + 1) * size * 4);
  }

  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

mkdirSync(OUT, { recursive: true });
for (const size of [16, 32, 48, 128]) {
  const file = join(OUT, `icon-${size}.png`);
  writeFileSync(file, png(size, render(size)));
  console.log(`wrote ${file}`);
}
