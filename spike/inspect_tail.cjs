// Inspect the tail of a DSH session log: last N events, types and key fields.
const fs = require('fs');
const z = require('zlib');

const file = process.argv[2];
const want = Number(process.argv[3] || 40);
const magic = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);

const buf = fs.readFileSync(file);
const offsets = [];
for (let i = 0; i <= buf.length - 4; i++) {
  if (buf[i] === 0x28 && buf[i + 1] === 0xb5 && buf[i + 2] === 0x2f && buf[i + 3] === 0xfd) offsets.push(i);
}

const events = [];
for (let k = offsets.length - 1; k >= 0 && events.length < want; k--) {
  let out = null;
  try {
    out = z.zstdDecompressSync(buf.subarray(offsets[k]));
  } catch {
    continue;
  }
  const lines = out.toString('utf8').split('\n').filter(Boolean);
  const parsed = [];
  for (const line of lines) {
    try {
      parsed.push(JSON.parse(line));
    } catch {
      /* skip */
    }
  }
  events.unshift(...parsed);
}

const tail = events.slice(-want);
console.log('frames', offsets.length, 'events', events.length, 'shown', tail.length);
for (const e of tail) {
  const t = e.type || '?';
  const d = e.data || {};
  let hint = '';
  if (t === 'user/message') hint = JSON.stringify(d.message || d).slice(0, 120);
  else if (t === 'assistant/message') hint = JSON.stringify(d).slice(0, 120);
  else if (t.startsWith('tool/')) hint = JSON.stringify(d).slice(0, 120);
  else hint = JSON.stringify(d).slice(0, 100);
  console.log(String(e.seq).padStart(4), e.time, t, hint);
}
