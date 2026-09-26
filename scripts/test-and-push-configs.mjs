#!/usr/bin/env node
// test-and-push-configs.mjs
//
// Run by .github/workflows/test-configs.yml after the bot dispatches a
// `test-configs` repository_dispatch event. For every extracted config in
// the batch file:
//   1. builds a REAL xray-core client config (same shape the bot itself
//      exports — see config-lib.mjs, ported verbatim from worker.js)
//   2. runs the actual xray-core binary against it, with a local SOCKS5
//      inbound
//   3. downloads through that SOCKS5 proxy, capped at ~0.5MB, to actually
//      exercise the real VMess/VLESS/Trojan/Shadowsocks protocol —
//      nothing here is a plain HTTP guess the way the old in-Worker
//      version had to be; this speaks the real protocol because it's
//      literally running the real client.
// Nothing is filtered based on the test result — every extracted config
// still gets renamed and pushed, exactly like before. The test is purely
// informational, reported back to the admin over Telegram.
//
// Hysteria2 is skipped for the live-download step (xray-core doesn't
// support it — different transport entirely) but is still pushed.

import { spawn } from 'node:child_process';
import { readFile, writeFile, rm } from 'node:fs/promises';
import { existsSync, mkdirSync } from 'node:fs';
import { parseConfig, serializeConfig, toXrayConfigJson } from './config-lib.mjs';

const BATCH_FILE = process.env.BATCH_FILE; // repo-relative path, e.g. pending-tests/173...json
const CONFIG_PATH = process.env.CONFIG_PATH || 'Telegram-Channel-@I_w_a_n_a.txt';
const ADMIN_CHAT_ID = process.env.ADMIN_CHAT_ID || '';
const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || '';
const WORKER_BASE_URL = process.env.WORKER_BASE_URL || ''; // e.g. https://your-worker.workers.dev
const WORKER_INTERNAL_SECRET = process.env.WORKER_INTERNAL_SECRET || '';
const XRAY_BIN = process.env.XRAY_BIN || './xray-bin/xray';

const PUSHED_CONFIG_REMARK = '🇩🇪 @I_w_a_n_a'; // MUST stay in sync with worker.js's PUSHED_CONFIG_REMARK
const TEST_DOWNLOAD_URL = 'https://speed.hetzner.de/100MB.bin';
const TEST_MAX_BYTES = 512 * 1024; // ~0.5MB cap
const TEST_TIMEOUT_MS = 15000;
const XRAY_STARTUP_MS = 1200;
const CONCURRENCY = 5;
const BASE_SOCKS_PORT = 19000;

function log(...args) {
  console.log(new Date().toISOString(), ...args);
}

async function sendTelegram(text) {
  if (!TELEGRAM_BOT_TOKEN || !ADMIN_CHAT_ID) {
    log('telegram not configured, skipping notification:', text);
    return;
  }
  try {
    const res = await fetch(`https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chat_id: ADMIN_CHAT_ID, text, parse_mode: 'HTML' }),
    });
    if (!res.ok) log('telegram send failed', res.status, await res.text().catch(() => ''));
  } catch (e) {
    log('telegram send error', e);
  }
}

function escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Runs one config through a real xray-core process and attempts to
// download up to TEST_MAX_BYTES through it. Returns
// { bytes, ok, error, debug } — debug carries xray's own log tail plus
// curl's exit code/stderr/http-code so a 0-byte result is diagnosable
// instead of just "it didn't work". Never throws — every failure mode is
// caught and reported as ok:false.
async function testOneConfig(cfg, socksPort) {
  if (cfg.protocol === 'hysteria2') {
    return { bytes: 0, ok: false, error: 'hysteria2_not_supported_by_xray_core' };
  }

  let xrayConfigJson;
  try {
    xrayConfigJson = toXrayConfigJson(cfg, socksPort, 'debug');
  } catch (e) {
    return { bytes: 0, ok: false, error: String(e?.message || e) };
  }

  const configFile = `/tmp/xray-cfg-${socksPort}.json`;
  await writeFile(configFile, JSON.stringify(xrayConfigJson));

  const xray = spawn(XRAY_BIN, ['run', '-c', configFile]);
  let xrayExited = false;
  let xrayExitInfo = '';
  let xrayLog = '';
  const captureXray = (d) => { xrayLog = (xrayLog + d.toString()).slice(-4000); };
  xray.stdout.on('data', captureXray);
  xray.stderr.on('data', captureXray);
  xray.on('exit', (code, signal) => { xrayExited = true; xrayExitInfo = `code=${code} signal=${signal}`; });

  try {
    await new Promise((resolve) => setTimeout(resolve, XRAY_STARTUP_MS));
    if (xrayExited) {
      return { bytes: 0, ok: false, error: `xray_exited_immediately (${xrayExitInfo})`, debug: { xrayLog } };
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TEST_TIMEOUT_MS);
    try {
      // Node's built-in fetch doesn't support a SOCKS proxy directly, so
      // we shell out to curl (present on all GitHub-hosted Ubuntu runners)
      // instead of pulling in a proxy-agent dependency for this.
      const { bytes, httpCode, curlExitCode, curlErr } = await new Promise((resolve, reject) => {
        const curl = spawn('curl', [
          '-x', `socks5h://127.0.0.1:${socksPort}`,
          '-m', String(Math.ceil(TEST_TIMEOUT_MS / 1000)),
          '--max-filesize', String(TEST_MAX_BYTES),
          '-o', '/dev/null',
          '-s', '-S',
          '-w', '%{http_code} %{size_download}',
          TEST_DOWNLOAD_URL,
        ]);
        let out = '';
        let err = '';
        curl.stdout.on('data', (d) => { out += d.toString(); });
        curl.stderr.on('data', (d) => { err += d.toString(); });
        curl.on('error', reject);
        curl.on('exit', (code) => {
          const [httpCodeStr, bytesStr] = out.trim().split(/\s+/);
          const n = parseInt(bytesStr, 10);
          resolve({
            bytes: Number.isFinite(n) ? n : 0,
            httpCode: httpCodeStr || '',
            curlExitCode: code,
            curlErr: err.trim().slice(0, 300),
          });
        });
      });
      clearTimeout(timer);
      return { bytes, ok: bytes > 0, debug: { xrayLog, httpCode, curlExitCode, curlErr } };
    } finally {
      clearTimeout(timer);
    }
  } catch (e) {
    return { bytes: 0, ok: false, error: String(e?.message || e), debug: { xrayLog } };
  } finally {
    try { xray.kill('SIGKILL'); } catch {}
    await rm(configFile, { force: true }).catch(() => {});
  }
}

async function runWithConcurrency(items, limit, fn) {
  const results = new Array(items.length);
  let i = 0;
  async function worker() {
    while (i < items.length) {
      const idx = i++;
      results[idx] = await fn(items[idx], BASE_SOCKS_PORT + (idx % limit));
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

async function main() {
  if (!BATCH_FILE) {
    console.error('BATCH_FILE env var not set');
    process.exit(1);
  }
  if (!existsSync(XRAY_BIN)) {
    console.error(`xray binary not found at ${XRAY_BIN} — did the download step run?`);
    process.exit(1);
  }

  const raw = await readFile(BATCH_FILE, 'utf8');
  const found = JSON.parse(raw); // [{ raw, protocol }, ...]
  log(`loaded ${found.length} config(s) from ${BATCH_FILE}`);

  const parsed = found.map((item) => {
    try {
      return { item, cfg: parseConfig(item.raw) };
    } catch (e) {
      return { item, cfg: null, parseError: String(e?.message || e) };
    }
  });

  const testable = parsed.filter((p) => p.cfg);
  const results = await runWithConcurrency(testable, CONCURRENCY, (p, port) => testOneConfig(p.cfg, port));
  testable.forEach((p, idx) => { p.testResult = results[idx]; });

  // Rename every successfully-parsed config's remark and re-serialize —
  // same as renameConfigRemark() used to do inside worker.js. Anything
  // that failed to parse falls back to its original raw line untouched
  // (better an unrenamed but working config than dropping it).
  const renamedLines = parsed.map((p) => {
    if (!p.cfg) return p.item.raw;
    try {
      p.cfg.remark = PUSHED_CONFIG_REMARK;
      return serializeConfig(p.item.protocol, p.cfg);
    } catch {
      return p.item.raw;
    }
  });

  // Merge into the target pool file, de-duplicated — same logic as
  // pushExtractedConfigsToGithub used to do in worker.js.
  let existingText = '';
  try {
    existingText = await readFile(CONFIG_PATH, 'utf8');
  } catch {
    existingText = '';
  }
  const existingLines = existingText.split('\n').map((l) => l.trim()).filter(Boolean);
  const seen = new Set(existingLines);
  const newLines = renamedLines.filter((line) => {
    if (seen.has(line)) return false;
    seen.add(line);
    return true;
  });

  if (newLines.length) {
    const merged = [...existingLines, ...newLines].join('\n') + '\n';
    await writeFile(CONFIG_PATH, merged, 'utf8');
  }

  // Clean up the batch file — it's only ever meant to live until this run
  // processes it.
  await rm(BATCH_FILE, { force: true }).catch(() => {});

  // Tell the Worker's D1 about the newly-pushed lines so the existing 24h
  // auto-expiry cron (cleanupExpiredConfigs) still picks these up — this
  // is a best-effort call; if it fails the configs are still correctly
  // pushed, they just won't auto-expire, which gets flagged to the admin
  // below instead of failing the whole run.
  let recordedForExpiry = false;
  if (newLines.length && WORKER_BASE_URL && WORKER_INTERNAL_SECRET) {
    try {
      const res = await fetch(`${WORKER_BASE_URL.replace(/\/$/, '')}/internal/record-pushed-configs`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-internal-secret': WORKER_INTERNAL_SECRET },
        body: JSON.stringify({ lines: newLines }),
      });
      recordedForExpiry = res.ok;
      if (!res.ok) log('record-pushed-configs failed', res.status, await res.text().catch(() => ''));
    } catch (e) {
      log('record-pushed-configs error', e);
    }
  }

  // ---- Summary for the admin ----
  const totalBytes = testable.reduce((sum, p) => sum + (p.testResult?.bytes || 0), 0);
  const gotData = testable.filter((p) => p.testResult?.ok).length;
  const parseFailed = parsed.length - testable.length;

  const lines = [
    `✅ دسته‌ی extract-sub پردازش شد.`,
    `📦 ${parsed.length} کانفیگ استخراج‌شده → ${newLines.length} تا جدید (${parsed.length - newLines.length - parseFailed} تکراری) به گیت‌هاب اضافه شد.`,
    `🔌 تست واقعی از طریق xray-core: روی ${gotData} از ${testable.length} کانفیگ دیتا واقعی دانلود شد — مجموعاً ${(totalBytes / 1024).toFixed(0)}KB.`,
  ];
  if (parseFailed) lines.push(`⚠️ ${parseFailed} خط قابل پارس نبود (بدون تغییر، همون‌طوری که بود اضافه شد).`);

  // Show why, not just that it failed — the first failed test's debug
  // info (xray's own log tail, curl's exit code/http-code/stderr), so a
  // 0-byte result is actually actionable instead of a dead end.
  if (testable.length && gotData < testable.length) {
    const failedSample = testable.find((p) => !p.testResult?.ok);
    const d = failedSample?.testResult?.debug || {};
    const err = failedSample?.testResult?.error;
    lines.push('');
    lines.push(`🩺 دیباگ (نمونه‌ی اول ناموفق — ${failedSample?.cfg?.server}:${failedSample?.cfg?.port}):`);
    if (err) lines.push(`error: <code>${escapeHtml(err)}</code>`);
    if (d.httpCode !== undefined) lines.push(`curl http_code: <code>${escapeHtml(String(d.httpCode))}</code>, exit: <code>${escapeHtml(String(d.curlExitCode))}</code>`);
    if (d.curlErr) lines.push(`curl stderr: <code>${escapeHtml(d.curlErr)}</code>`);
    if (d.xrayLog) lines.push(`xray log (آخرین بخش): <code>${escapeHtml(d.xrayLog.slice(-1800))}</code>`);
  }

  if (newLines.length && WORKER_BASE_URL && !recordedForExpiry) {
    lines.push(`⚠️ ثبت این دسته برای پاکسازی خودکار ۲۴ساعته انجام نشد (خطا در تماس با ورکر) — این کانفیگ‌ها دستی باید حذف بشن اگه لازمه.`);
  }
  await sendTelegram(lines.join('\n'));
  log('done', { total: parsed.length, added: newLines.length, gotData, totalBytes });
}

main().catch(async (e) => {
  console.error(e);
  await sendTelegram(`❌ اجرای تست extract-sub با خطا متوقف شد:\n<code>${escapeHtml(String(e?.message || e)).slice(0, 500)}</code>`);
  process.exit(1);
});
