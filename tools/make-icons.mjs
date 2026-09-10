/**
 * Generate the extension icons as PNGs, with no image library.
 *
 * Placeholder art, but real files: Chrome refuses to load an extension whose
 * declared icons are missing, and a designed icon is a launch task, not a
 * phase-one task. Replace assets/*.png whenever the real artwork exists.
 *
 * The geometry is supersampled rather than tested once per pixel. At 16 pixels
 * the difference is not subtle — that is the size the toolbar actually shows.
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
const CAP = 0.5;     // bar end rounding, as a fraction of the bar's half-width

/**
 * Samples per axis when rendering.
 *
 * Everything below is a hard in-or-out test, which at 16 pixels produces bars
 * with visibly ragged ends and a corner that stair-steps. Rendering the same
 * geometry at four times the resolution and averaging is the whole of the fix:
 * a pixel straddling an edge gets the fraction of it that is actually covered.
 */
const SS = 4;

/** Whether a point, in fractions of the canvas, is inside the rounded square. */
function inTile(fx, fy) {
  const cx = Math.min(fx, 1 - fx);
  const cy = Math.min(fy, 1 - fy);
  if (cx >= RADIUS || cy >= RADIUS) return true;
  return (RADIUS - cx) ** 2 + (RADIUS - cy) ** 2 <= RADIUS * RADIUS;
}

/**
 * Whether a point is inside one of the bars.
 *
 * The bars are stadiums rather than rectangles: square ends read as a bar
 * chart, and rounded ones as a level meter, which is what this is meant to be.
 */
function inBar(fx, fy) {
  for (const [bx, bh] of BARS) {
    const halfW = BAR_W / 2;
    if (Math.abs(fx - bx) > halfW) continue;
    if (Math.abs(fy - 0.5) > bh) continue;

    const capR = halfW * CAP * 2;
    const flat = bh - capR;
    const dy = Math.abs(fy - 0.5);
    if (dy <= flat) return true;

    // Inside a cap: distance to the centre of the end circle.
    const dx = Math.abs(fx - bx);
    if (dx <= halfW - capR) return true;
    const ox = dx - (halfW - capR);
    const oy = dy - flat;
    if (ox * ox + oy * oy <= capR * capR) return true;
  }
  return false;
}

/** @returns {Buffer} raw RGBA pixels, row-major */
function render(size) {
  const px = Buffer.alloc(size * size * 4);
  const per = SS * SS;

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let tile = 0;
      let bar = 0;

      for (let sy = 0; sy < SS; sy++) {
        for (let sx = 0; sx < SS; sx++) {
          const fx = (x + (sx + 0.5) / SS) / size;
          const fy = (y + (sy + 0.5) / SS) / size;
          if (!inTile(fx, fy)) continue;
          tile++;
          if (inBar(fx, fy)) bar++;
        }
      }

      if (!tile) continue; // fully outside: leave transparent

      // Coverage decides alpha; the bar's share of the covered part decides
      // colour. Doing it in that order keeps a bar end that lands on the tile
      // edge from picking up a fringe of background.
      const t = bar / tile;
      const i = (y * size + x) * 4;
      px[i] = Math.round(BG[0] + (FG[0] - BG[0]) * t);
      px[i + 1] = Math.round(BG[1] + (FG[1] - BG[1]) * t);
      px[i + 2] = Math.round(BG[2] + (FG[2] - BG[2]) * t);
      px[i + 3] = Math.round((tile / per) * 255);
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
