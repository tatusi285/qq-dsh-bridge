#!/usr/bin/env node
/**
 * Read a DSH session log (read-only) and print its current status as JSON.
 *
 * The log is a sequence of independent zstd frames, each holding one or more
 * JSONL events. We scan backwards from the tail until the current turn's
 * `turn/start` is found, so a long session costs a few frames, not the file.
 *
 * Usage:
 *   node session_status.cjs --session <session-id> [--json]
 *   node session_status.cjs --file <path> [--json]
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const z = require('zlib');

const MAGIC = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);

function frameOffsets(buf) {
  const out = [];
  for (let i = 0; i <= buf.length - 4; i++) {
    if (buf[i] === 0x28 && buf[i + 1] === 0xb5 && buf[i + 2] === 0x2f && buf[i + 3] === 0xfd) out.push(i);
  }
  return out;
}

function decodeFrame(buf, start, end) {
  const slice = end === undefined ? buf.subarray(start) : buf.subarray(start, end);
  try {
    return z.zstdDecompressSync(slice).toString('utf8');
  } catch {
    return null;
  }
}

function findSessionFile(sessionId) {
  const root = path.join(os.homedir(), '.dsh', 'sessions');
  if (!fs.existsSync(root)) return null;
  for (const bucket of fs.readdirSync(root, { withFileTypes: true })) {
    if (!bucket.isDirectory()) continue;
    const dir = path.join(root, bucket.name, sessionId);
    const file = path.join(dir, 'session.v4.jsonl.zstd');
    if (fs.existsSync(file)) return file;
  }
  return null;
}

function readHead(file) {
  const buf = fs.readFileSync(file);
  const offsets = frameOffsets(buf);
  const events = [];
  for (let k = 0; k < Math.min(20, offsets.length); k++) {
    const text = decodeFrame(buf, offsets[k], offsets[k + 1]);
    if (text === null) continue;
    for (const line of text.split('\n')) {
      if (!line) continue;
      try {
        events.push(JSON.parse(line));
      } catch {
        /* torn line */
      }
    }
  }
  return events;
}

function readTail(file, minEvents = 120) {
  const buf = fs.readFileSync(file);
  const offsets = frameOffsets(buf);
  if (offsets.length === 0) return { events: [], frames: 0, bytes: buf.length };

  // Walk frames backwards until we cover the latest turn start plus some context.
  let events = [];
  let sawTurnStart = false;
  let extraFrames = 0;
  for (let k = offsets.length - 1; k >= 0; k--) {
    const text = decodeFrame(buf, offsets[k], offsets[k + 1]);
    if (text === null) continue;
    const chunk = [];
    for (const line of text.split('\n')) {
      if (!line) continue;
      try {
        chunk.push(JSON.parse(line));
      } catch {
        /* torn line */
      }
    }
    events = chunk.concat(events);
    if (chunk.some((e) => e.type === 'turn/start')) sawTurnStart = true;
    if (sawTurnStart && events.length >= minEvents) {
      // Keep walking back a little so the human's own last message stays in
      // view even when the current turn is long.
      extraFrames += 1;
      if (extraFrames >= 24) break;
    }
    if (!sawTurnStart && events.length >= 4000) break; // safety cap
  }
  return { events, frames: offsets.length, bytes: buf.length };
}

function textOf(content) {
  if (!Array.isArray(content)) return '';
  return content
    .filter((c) => c && c.type === 'text' && typeof c.text === 'string')
    .map((c) => c.text)
    .join('\n')
    .trim();
}

// Harness-injected user-role messages (goal rounds, runtime context, policy
// notices) are not what the human said; keep them out of the "你最后说" line.
const CONTEXT_MARKERS = [
  'Time sampled',
  'Elapsed since',
  'Browser time zone',
  'Current runtime context',
  'Current working directory',
  'Current DSH file policy',
  'Approval prompts are disabled',
  'Current DeepSeek Harness',
  'You are an AI agent',
  'Tokens prefixed with',
];

function firstParagraph(text) {
  const parts = text.split(/\n{2,}/);
  for (const part of parts) {
    const t = part.trim();
    if (t) return t;
  }
  return '';
}

function isInjectedUserText(text) {
  const first = firstParagraph(text);
  if (!first) return true;
  if (first.startsWith('<')) return true; // goal round, system, runtime blocks
  if (first.startsWith('The approval policy changed')) return true;
  return CONTEXT_MARKERS.some((marker) => first.startsWith(marker));
}

// Drop trailing runtime-context paragraphs from a real human message.
function humanText(text) {
  const kept = text
    .split(/\n{2,}/)
    .filter((paragraph) => {
      const t = paragraph.trimStart();
      if (!t) return false;
      return !CONTEXT_MARKERS.some((marker) => t.startsWith(marker));
    });
  return kept.join('\n\n').trim();
}

function toolHint(argsJson) {
  if (typeof argsJson !== 'string') return '';
  try {
    const args = JSON.parse(argsJson);
    for (const key of ['description', 'command', 'path', 'file_path', 'query', 'task', 'pattern']) {
      if (typeof args[key] === 'string' && args[key].trim()) {
        return args[key].trim().replace(/\s+/g, ' ').slice(0, 100);
      }
    }
    return JSON.stringify(args).slice(0, 100);
  } catch {
    return argsJson.slice(0, 100);
  }
}

function buildStatus(events, meta) {
  const turnStarts = events.filter((e) => e.type === 'turn/start');
  const lastStart = turnStarts[turnStarts.length - 1] || null;
  const startSeq = lastStart ? lastStart.seq : -1;
  const turnEvents = events.filter((e) => typeof e.seq === 'number' && e.seq > startSeq);
  const currentEnd = turnEvents.find((e) => e.type === 'turn/end') || null;

  // The last *completed* turn may belong to an earlier turn than the one now
  // running; completion notifications key off it, so look at the whole tail.
  const ends = events.filter((e) => e.type === 'turn/end');
  const turnEnd = ends.length ? ends[ends.length - 1] : null;
  const endStart = turnEnd
    ? [...events].reverse().find((e) => e.type === 'turn/start' && e.data && e.data.turn === turnEnd.data.turn)
    : null;

  const running = Boolean(lastStart) && !currentEnd;
  const turn = lastStart ? lastStart.data.turn : null;

  let step = null;
  let currentTool = null;
  let lastText = '';
  let lastUser = '';
  let deliverables = [];
  const openCalls = new Map();

  for (const e of turnEvents) {
    const d = e.data || {};
    if (e.type === 'step/start') step = d.step;
    if (e.type === 'tool/call') {
      const call = { name: d.name, hint: toolHint(d.arguments), callId: d.callId, at: e.time };
      openCalls.set(d.callId, call);
      currentTool = call;
    }
    if (e.type === 'tool/result') {
      const callId = (d.message && d.message.toolCallId) || (d.message && d.message.source && d.message.source.callId) || d.toolCallId;
      if (callId && openCalls.has(callId)) openCalls.delete(callId);
      if (currentTool && currentTool.callId === callId) currentTool = null;
    }
    if (e.type === 'assistant/message') {
      const t = textOf((d.message || {}).content);
      if (t) lastText = t;
    }
    if (e.type === 'user/message') {
      const raw = textOf(d.content);
      if (raw && !isInjectedUserText(raw)) {
        lastUser = humanText(raw) || raw;
      }
    }
    if (e.type === 'deliverables/presented' && Array.isArray(d.files)) {
      deliverables = d.files.slice(0, 5);
    }
  }

  // The current turn may contain only harness-injected user text (goal round,
  // runtime context); fall back to the newest real human message in the tail.
  if (!lastUser) {
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.type !== 'user/message') continue;
      const raw = textOf((e.data || {}).content);
      if (raw && !isInjectedUserText(raw)) {
        lastUser = humanText(raw) || raw;
        break;
      }
    }
  }

  const head = meta.headEvents || [];
  const all = head.concat(events);
  const lastOf = (type) => {
    let found = null;
    for (const e of all) {
      if (e.type === type && (!found || (e.seq || 0) >= (found.seq || 0))) found = e;
    }
    return found;
  };
  const approvalPolicy = lastOf('approval/policy');
  const permissionPreset = lastOf('permission/preset');
  const lastEvent = events[events.length - 1] || null;

  return {
    ok: true,
    sessionId: meta.sessionId,
    cwd: meta.cwd,
    title: meta.title || null,
    file: meta.file,
    running,
    turn,
    step,
    turnStartedAt: lastStart ? lastStart.time : null,
    elapsedMs: lastStart && running ? Date.now() - lastStart.time : null,
    currentTool: running ? currentTool : null,
    pendingTools: running ? [...openCalls.values()] : [],
    lastText: lastText.slice(0, 600),
    lastUserMessage: lastUser.slice(0, 300),
    lastTurnEnd: turnEnd ? { turn: turnEnd.data.turn, reason: turnEnd.data.reason, at: turnEnd.time } : null,
    lastTurnDurationMs: turnEnd && endStart ? turnEnd.time - endStart.time : null,
    approvalPolicy: approvalPolicy ? approvalPolicy.data.policy : null,
    permissionPreset: permissionPreset ? permissionPreset.data.preset : null,
    deliverables,
    eventSeq: lastEvent ? lastEvent.seq : null,
    updatedAt: lastEvent ? lastEvent.time : null,
    frames: meta.frames,
    bytes: meta.bytes,
  };
}

function main() {
  const argv = process.argv.slice(2);
  const get = (flag) => {
    const i = argv.indexOf(flag);
    return i >= 0 ? argv[i + 1] : null;
  };
  const sessionId = get('--session');
  let file = get('--file');
  if (!file && sessionId) file = findSessionFile(sessionId);
  if (!file) {
    console.error('session not found');
    process.exit(2);
  }

  const headEvents = readHead(file);
  const { events, frames, bytes } = readTail(file);

  let cwd = null;
  let title = null;
  let sid = sessionId;
  for (const e of headEvents.concat(events)) {
    if (e.type === 'session' && !cwd) {
      cwd = e.cwd;
      sid = sid || e.id;
    }
    if (e.type === 'session/title' && !title) title = e.data && e.data.title;
  }

  const status = buildStatus(events, { sessionId: sid, cwd, title, file, frames, bytes, headEvents });
  if (argv.includes('--pretty')) {
    console.log(JSON.stringify(status, null, 2));
  } else {
    console.log(JSON.stringify(status));
  }
}

main();
