// Print one sample per event type from a DSH session log.
const fs = require('fs');
const z = require('zlib');

const file = process.argv[2];
const buf = fs.readFileSync(file);
const magic = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);
const offsets = [];
for (let i = 0; i <= buf.length - 4; i++) {
  if (buf[i] === 0x28 && buf[i + 1] === 0xb5 && buf[i + 2] === 0x2f && buf[i + 3] === 0xfd) offsets.push(i);
}

const seen = new Map();
let total = 0;
for (const off of offsets) {
  let out;
  try {
    out = z.zstdDecompressSync(buf.subarray(off));
  } catch {
    continue;
  }
  for (const line of out.toString('utf8').split('\n')) {
    if (!line) continue;
    let e;
    try {
      e = JSON.parse(line);
    } catch {
      continue;
    }
    total++;
    const t = e.type || '?';
    if (!seen.has(t)) seen.set(t, e);
  }
}

console.log('frames', offsets.length, 'events', total, 'types', seen.size);
for (const [t, e] of seen) {
  console.log('\n--- ' + t + ' ---');
  console.log(JSON.stringify(e).slice(0, 500));
}
