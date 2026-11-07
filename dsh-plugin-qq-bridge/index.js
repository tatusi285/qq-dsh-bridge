// QQ bridge control surface, mounted inside the DeepSeek Harness host process.
//
// The bridge never opens a session for writing: it talks to the agent that is
// already live in this process (`ctx.agents`), so it cannot conflict with the
// desktop and needs no ACP resume. Endpoints are localhost-only and gated by a
// token read from ~/.dsh/qq-bridge/config.json.
//
//   GET  /ping                 liveness + how many live agents exist
//   GET  /agents               live root agents (id + status)
//   POST /say {text, sessionId?}   queue a user message into that agent
//
// Queueing uses `agent.followup(...)`, the same inbox path the composer uses,
// so a running turn keeps going and the message lands at the next turn.

import { createServer } from 'node:http';
import { appendFileSync, mkdirSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const DEFAULT_PORT = 8770;

function bridgeDir() {
  return join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'qq-bridge');
}

function loadConfig() {
  try {
    return JSON.parse(readFileSync(join(bridgeDir(), 'config.json'), 'utf8'));
  } catch {
    return {};
  }
}

function log(message) {
  try {
    mkdirSync(bridgeDir(), { recursive: true });
    appendFileSync(
      join(bridgeDir(), 'bridge.log'),
      `[${new Date().toISOString()}] ${message}\n`,
      'utf8',
    );
  } catch {
    /* logging must never break the host */
  }
}

function readBody(req, limit = 1_000_000) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > limit) {
        reject(new Error('body too large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

export function apply(ctx) {
  const config = loadConfig();
  const port = Number(config.port || DEFAULT_PORT);
  const token = String(config.token || '');

  const pickAgent = (sessionId) => {
    if (sessionId) return ctx.agents.get(String(sessionId));
    const roots = ctx.agents.roots();
    return roots.length ? roots[roots.length - 1] : undefined;
  };

  const send = (res, code, payload) => {
    const body = JSON.stringify(payload);
    res.writeHead(code, {
      'content-type': 'application/json; charset=utf-8',
      'content-length': Buffer.byteLength(body),
    });
    res.end(body);
  };

  const server = createServer(async (req, res) => {
    try {
      if (token && req.headers['x-bridge-token'] !== token) {
        send(res, 401, { ok: false, error: 'bad token' });
        return;
      }

      const url = new URL(req.url || '/', 'http://127.0.0.1');

      if (req.method === 'GET' && url.pathname === '/ping') {
        send(res, 200, { ok: true, agents: ctx.agents.roots().length, pid: process.pid });
        return;
      }

      if (req.method === 'GET' && url.pathname === '/agents') {
        const agents = ctx.agents.roots().map((agent) => ({
          id: agent.id,
          status: typeof agent.status === 'string' ? agent.status : undefined,
        }));
        send(res, 200, { ok: true, agents });
        return;
      }

      if (req.method === 'POST' && url.pathname === '/say') {
        const raw = await readBody(req);
        let payload;
        try {
          payload = JSON.parse(raw || '{}');
        } catch {
          send(res, 400, { ok: false, error: 'invalid json' });
          return;
        }
        const text = typeof payload.text === 'string' ? payload.text.trim() : '';
        if (!text) {
          send(res, 400, { ok: false, error: 'empty text' });
          return;
        }
        const agent = pickAgent(payload.sessionId);
        if (!agent) {
          send(res, 404, { ok: false, error: 'no live agent' });
          return;
        }
        await agent.followup({
          content: [{ type: 'text', text }],
          source: { kind: 'qq-bridge' },
        });
        log(`queued ${text.length} chars into ${agent.id}`);
        send(res, 200, { ok: true, queued: true, sessionId: agent.id });
        return;
      }

      send(res, 404, { ok: false, error: 'not found' });
    } catch (error) {
      log(`request failed: ${error && error.message ? error.message : String(error)}`);
      try {
        send(res, 500, { ok: false, error: 'internal error' });
      } catch {
        /* response already gone */
      }
    }
  });

  server.on('error', (error) => log(`listen failed: ${error.message}`));
  server.listen(port, '127.0.0.1', () => log(`listening on 127.0.0.1:${port}`));

  ctx.effect(() => () => {
    log('disposing');
    try {
      server.close();
    } catch {
      /* already closed */
    }
  });
}
