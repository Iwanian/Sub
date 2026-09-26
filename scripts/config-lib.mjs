// config-lib.mjs
//
// Standalone Node.js port of the config-parsing + xray-core-JSON-export
// logic that already lives inside worker.js (src/services/configs/*).
// Kept byte-for-byte equivalent on purpose so a config that parses/exports
// one way in the bot behaves identically here in the test workflow — this
// is NOT a reimplementation, it's the same functions copied out verbatim
// (they use only standard Web APIs: atob/btoa, URL, URLSearchParams,
// TextEncoder/TextDecoder — all available natively in Node 18+, no changes
// needed).
//
// If you ever change how worker.js parses/serializes a protocol, copy the
// same change over here too (or, better, extract both from one shared
// source file next time you touch this).

// ---- from src/utils/base64.js ----
function b64Decode(input) {
  let str = input.replace(/-/g, '+').replace(/_/g, '/').trim();
  while (str.length % 4 !== 0) str += '=';
  const binary = atob(str);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new TextDecoder('utf-8', { fatal: false }).decode(bytes);
}

function b64Encode(str) {
  const bytes = new TextEncoder().encode(str);
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

function tryB64Decode(input) {
  try {
    return b64Decode(input);
  } catch {
    return null;
  }
}

// ---- from src/services/configs/detect.js ----
const SCHEME_MAP = [
  { proto: 'vmess', prefix: 'vmess://' },
  { proto: 'vless', prefix: 'vless://' },
  { proto: 'trojan', prefix: 'trojan://' },
  { proto: 'shadowsocks', prefix: 'ss://' },
  { proto: 'hysteria2', prefix: 'hysteria2://' },
  { proto: 'hysteria2', prefix: 'hy2://' },
];

function detectProtocol(raw) {
  const trimmed = (raw || '').trim();
  for (const { proto, prefix } of SCHEME_MAP) {
    if (trimmed.toLowerCase().startsWith(prefix)) return proto;
  }
  return null;
}

// ---- from src/services/configs/model.js ----
function emptyConfig(protocol) {
  return { protocol, remark: '', server: '', port: 443, tls: false, extra: {} };
}

// ---- from src/services/configs/protocols/vmess.js ----
function parseVmess(raw) {
  const body = raw.trim().slice('vmess://'.length);
  const decoded = tryB64Decode(body);
  if (!decoded) throw new Error('vmess_decode_failed');
  let json;
  try {
    json = JSON.parse(decoded);
  } catch {
    throw new Error('vmess_json_invalid');
  }
  const cfg = emptyConfig('vmess');
  cfg.remark = json.ps || '';
  cfg.server = json.add;
  cfg.port = parseInt(json.port, 10);
  cfg.uuid = json.id;
  cfg.network = json.net || 'tcp';
  cfg.tls = (json.tls || '') === 'tls';
  cfg.sni = json.sni || json.host || undefined;
  cfg.path = json.path || undefined;
  cfg.host = json.host || undefined;
  cfg.extra = { aid: json.aid || 0, type: json.type || 'none', scy: json.scy || 'auto', v: json.v || '2' };
  if (!cfg.server || !cfg.port || !cfg.uuid) throw new Error('vmess_missing_fields');
  return cfg;
}

function serializeVmess(cfg) {
  const json = {
    v: cfg.extra?.v || '2',
    ps: cfg.remark || '',
    add: cfg.server,
    port: String(cfg.port),
    id: cfg.uuid,
    aid: String(cfg.extra?.aid ?? 0),
    scy: cfg.extra?.scy || 'auto',
    net: cfg.network || 'tcp',
    type: cfg.extra?.type || 'none',
    host: cfg.host || '',
    path: cfg.path || '',
    tls: cfg.tls ? 'tls' : '',
    sni: cfg.sni || '',
  };
  return 'vmess://' + b64Encode(JSON.stringify(json));
}

// ---- from src/services/configs/protocols/vless.js ----
function parseVless(raw) {
  const url = new URL(raw.trim());
  const cfg = emptyConfig('vless');
  cfg.uuid = decodeURIComponent(url.username);
  cfg.server = url.hostname;
  cfg.port = parseInt(url.port, 10);
  cfg.remark = decodeURIComponent(url.hash.replace('#', '')) || '';
  const p = url.searchParams;
  cfg.network = p.get('type') || 'tcp';
  cfg.tls = (p.get('security') || '') === 'tls' || (p.get('security') || '') === 'reality';
  cfg.sni = p.get('sni') || undefined;
  cfg.path = p.get('path') || undefined;
  cfg.host = p.get('host') || undefined;
  cfg.alpn = p.get('alpn') || undefined;
  cfg.extra = {
    flow: p.get('flow') || '',
    security: p.get('security') || '',
    pbk: p.get('pbk') || '',
    sid: p.get('sid') || '',
    fp: p.get('fp') || '',
    headerType: p.get('headerType') || '',
    serviceName: p.get('serviceName') || '',
    encryption: p.get('encryption') || 'none',
    mode: p.get('mode') || '',
    xhttpExtra: p.get('extra') || '',
    xPaddingBytes: p.get('x_padding_bytes') || '',
    pinnedCertSha256: p.get('pcs') || '',
    spx: p.get('spx') || '',
  };
  if (!cfg.server || !cfg.port || !cfg.uuid) throw new Error('vless_missing_fields');
  return cfg;
}

function serializeVless(cfg) {
  const p = new URLSearchParams();
  p.set('type', cfg.network || 'tcp');
  if (cfg.extra?.security) p.set('security', cfg.extra.security);
  else if (cfg.tls) p.set('security', 'tls');
  if (cfg.sni) p.set('sni', cfg.sni);
  if (cfg.path) p.set('path', cfg.path);
  if (cfg.host) p.set('host', cfg.host);
  if (cfg.alpn) p.set('alpn', cfg.alpn);
  if (cfg.extra?.flow) p.set('flow', cfg.extra.flow);
  if (cfg.extra?.pbk) p.set('pbk', cfg.extra.pbk);
  if (cfg.extra?.sid) p.set('sid', cfg.extra.sid);
  if (cfg.extra?.fp) p.set('fp', cfg.extra.fp);
  if (cfg.extra?.headerType) p.set('headerType', cfg.extra.headerType);
  if (cfg.extra?.serviceName) p.set('serviceName', cfg.extra.serviceName);
  p.set('encryption', cfg.extra?.encryption || 'none');
  if (cfg.extra?.mode) p.set('mode', cfg.extra.mode);
  if (cfg.extra?.xhttpExtra) p.set('extra', cfg.extra.xhttpExtra);
  if (cfg.extra?.xPaddingBytes) p.set('x_padding_bytes', cfg.extra.xPaddingBytes);
  if (cfg.extra?.pinnedCertSha256) p.set('pcs', cfg.extra.pinnedCertSha256);
  if (cfg.extra?.spx) p.set('spx', cfg.extra.spx);
  const query = p.toString();
  const remark = encodeURIComponent(cfg.remark || '');
  return `vless://${encodeURIComponent(cfg.uuid)}@${cfg.server}:${cfg.port}${query ? '?' + query : ''}#${remark}`;
}

// ---- from src/services/configs/protocols/trojan.js ----
function parseTrojan(raw) {
  const url = new URL(raw.trim());
  const cfg = emptyConfig('trojan');
  cfg.password = decodeURIComponent(url.username);
  cfg.server = url.hostname;
  cfg.port = parseInt(url.port, 10);
  cfg.remark = decodeURIComponent(url.hash.replace('#', '')) || '';
  const p = url.searchParams;
  cfg.network = p.get('type') || 'tcp';
  cfg.tls = true;
  cfg.sni = p.get('sni') || p.get('peer') || undefined;
  cfg.path = p.get('path') || undefined;
  cfg.host = p.get('host') || undefined;
  cfg.alpn = p.get('alpn') || undefined;
  cfg.extra = {
    allowInsecure: p.get('allowInsecure') || '0',
    headerType: p.get('headerType') || '',
    serviceName: p.get('serviceName') || '',
  };
  if (!cfg.server || !cfg.port || !cfg.password) throw new Error('trojan_missing_fields');
  return cfg;
}

function serializeTrojan(cfg) {
  const p = new URLSearchParams();
  p.set('type', cfg.network || 'tcp');
  if (cfg.sni) p.set('sni', cfg.sni);
  if (cfg.path) p.set('path', cfg.path);
  if (cfg.host) p.set('host', cfg.host);
  if (cfg.alpn) p.set('alpn', cfg.alpn);
  if (cfg.extra?.allowInsecure) p.set('allowInsecure', cfg.extra.allowInsecure);
  if (cfg.extra?.headerType) p.set('headerType', cfg.extra.headerType);
  if (cfg.extra?.serviceName) p.set('serviceName', cfg.extra.serviceName);
  const query = p.toString();
  const remark = encodeURIComponent(cfg.remark || '');
  return `trojan://${encodeURIComponent(cfg.password)}@${cfg.server}:${cfg.port}${query ? '?' + query : ''}#${remark}`;
}

// ---- from src/services/configs/protocols/shadowsocks.js ----
function splitFirst(str, sep) {
  const idx = str.indexOf(sep);
  if (idx < 0) return [str, ''];
  return [str.slice(0, idx), str.slice(idx + 1)];
}

function splitLastHostPort(hostPort) {
  const idx = hostPort.lastIndexOf(':');
  if (idx < 0) return [hostPort, ''];
  return [hostPort.slice(0, idx), hostPort.slice(idx + 1)];
}

function parseShadowsocks(raw) {
  const trimmed = raw.trim();
  const body = trimmed.slice('ss://'.length);
  const hashIdx = body.indexOf('#');
  const remark = hashIdx >= 0 ? decodeURIComponent(body.slice(hashIdx + 1)) : '';
  const main = hashIdx >= 0 ? body.slice(0, hashIdx) : body;

  const cfg = emptyConfig('shadowsocks');
  cfg.remark = remark;

  if (main.includes('@')) {
    const atIdx = main.lastIndexOf('@');
    const userInfoRaw = main.slice(0, atIdx);
    const hostPort = main.slice(atIdx + 1);
    const userInfo = tryB64Decode(userInfoRaw) || decodeURIComponent(userInfoRaw);
    const [method, password] = splitFirst(userInfo, ':');
    const [host, portStr] = splitLastHostPort(hostPort);
    cfg.method = method;
    cfg.password = password;
    cfg.server = host;
    cfg.port = parseInt(portStr, 10);
  } else {
    const decoded = tryB64Decode(main);
    if (!decoded) throw new Error('ss_decode_failed');
    const atIdx = decoded.lastIndexOf('@');
    if (atIdx < 0) throw new Error('ss_format_invalid');
    const [method, password] = splitFirst(decoded.slice(0, atIdx), ':');
    const [host, portStr] = splitLastHostPort(decoded.slice(atIdx + 1));
    cfg.method = method;
    cfg.password = password;
    cfg.server = host;
    cfg.port = parseInt(portStr, 10);
  }

  if (!cfg.server || !cfg.port || !cfg.method || !cfg.password) throw new Error('ss_missing_fields');
  return cfg;
}

function serializeShadowsocks(cfg) {
  const userInfo = b64Encode(`${cfg.method}:${cfg.password}`);
  const remark = encodeURIComponent(cfg.remark || '');
  return `ss://${userInfo}@${cfg.server}:${cfg.port}#${remark}`;
}

// ---- from src/services/configs/protocols/hysteria2.js ----
function parseHysteria2(raw) {
  const normalized = raw.trim().replace(/^hy2:\/\//i, 'hysteria2://');
  const url = new URL(normalized);
  const cfg = emptyConfig('hysteria2');
  cfg.password = decodeURIComponent(url.username);
  cfg.server = url.hostname;
  cfg.port = parseInt(url.port, 10) || 443;
  cfg.remark = decodeURIComponent(url.hash.replace('#', '')) || '';
  const p = url.searchParams;
  cfg.tls = true;
  cfg.sni = p.get('sni') || undefined;
  cfg.alpn = p.get('alpn') || undefined;
  cfg.extra = {
    insecure: p.get('insecure') || '0',
    obfs: p.get('obfs') || '',
    obfsPassword: p.get('obfs-password') || '',
    pinSHA256: p.get('pinSHA256') || '',
  };
  if (!cfg.server || !cfg.port || !cfg.password) throw new Error('hysteria2_missing_fields');
  return cfg;
}

function serializeHysteria2(cfg) {
  const p = new URLSearchParams();
  if (cfg.sni) p.set('sni', cfg.sni);
  if (cfg.alpn) p.set('alpn', cfg.alpn);
  if (cfg.extra?.insecure) p.set('insecure', cfg.extra.insecure);
  if (cfg.extra?.obfs) p.set('obfs', cfg.extra.obfs);
  if (cfg.extra?.obfsPassword) p.set('obfs-password', cfg.extra.obfsPassword);
  if (cfg.extra?.pinSHA256) p.set('pinSHA256', cfg.extra.pinSHA256);
  const query = p.toString();
  const remark = encodeURIComponent(cfg.remark || '');
  return `hysteria2://${encodeURIComponent(cfg.password)}@${cfg.server}:${cfg.port}${query ? '?' + query : ''}#${remark}`;
}

// ---- from src/services/configs/registry.js ----
const REGISTRY = {
  vmess: { parse: parseVmess, serialize: serializeVmess, label: 'VMess' },
  vless: { parse: parseVless, serialize: serializeVless, label: 'VLESS' },
  trojan: { parse: parseTrojan, serialize: serializeTrojan, label: 'Trojan' },
  shadowsocks: { parse: parseShadowsocks, serialize: serializeShadowsocks, label: 'Shadowsocks' },
  hysteria2: { parse: parseHysteria2, serialize: serializeHysteria2, label: 'Hysteria2' },
};

class ConfigParseError extends Error {}

function parseConfig(raw) {
  const proto = detectProtocol(raw);
  if (!proto || !REGISTRY[proto]) throw new ConfigParseError('unsupported_protocol');
  try {
    return REGISTRY[proto].parse(raw);
  } catch {
    throw new ConfigParseError('parse_failed');
  }
}

function serializeConfig(protocol, cfg) {
  if (!REGISTRY[protocol]) throw new ConfigParseError('unsupported_protocol');
  return REGISTRY[protocol].serialize(cfg);
}

// ---- from src/services/configs/exporters/xray.js ----
// Hysteria2 is NOT representable here (different transport entirely) —
// same limitation as in the bot; callers must skip it before calling this.
function buildStreamSettings(cfg) {
  const network = cfg.network || 'tcp';
  // security=reality needs its own distinct realitySettings block, NOT
  // tlsSettings — see the matching comment in worker.js's copy of this
  // function for why (this was a real bug, found via a live test run).
  const isReality = cfg.extra?.security === 'reality';
  const stream = { network, security: isReality ? 'reality' : cfg.tls ? 'tls' : 'none' };

  if (network === 'ws') {
    stream.wsSettings = { path: cfg.path || '/', headers: cfg.host ? { Host: cfg.host } : {} };
  } else if (network === 'grpc') {
    stream.grpcSettings = { serviceName: cfg.extra?.serviceName || '' };
  } else if (network === 'xhttp') {
    let extra = { mode: cfg.extra?.mode || 'auto', xPaddingBytes: cfg.extra?.xPaddingBytes || '100-1000' };
    if (cfg.extra?.xhttpExtra) {
      try {
        extra = JSON.parse(cfg.extra.xhttpExtra);
      } catch {
        /* keep default */
      }
    }
    stream.xhttpSettings = { path: cfg.path || '/', host: cfg.host || cfg.server, mode: cfg.extra?.mode || 'auto', extra };
  }

  if (stream.security === 'reality') {
    stream.realitySettings = {
      show: false,
      fingerprint: cfg.extra?.fp || 'chrome',
      serverName: cfg.sni || cfg.host || cfg.server,
      publicKey: cfg.extra?.pbk || '',
      shortId: cfg.extra?.sid || '',
      spiderX: cfg.extra?.spx || '',
    };
  } else if (stream.security === 'tls') {
    stream.tlsSettings = { allowInsecure: cfg.extra?.insecure === '1', serverName: cfg.sni || cfg.host || cfg.server, show: false };
    if (cfg.alpn) stream.tlsSettings.alpn = cfg.alpn.split(',').map((s) => s.trim());
    if (cfg.extra?.fp) stream.tlsSettings.fingerprint = cfg.extra.fp;
    if (cfg.extra?.pinnedCertSha256) stream.tlsSettings.pinnedPeerCertSha256 = cfg.extra.pinnedCertSha256;
  }

  return stream;
}

function buildProxyOutbound(cfg) {
  const streamSettings = buildStreamSettings(cfg);
  const mux = { enabled: false, concurrency: -1, xudpConcurrency: 8, xudpProxyUDP443: '' };

  switch (cfg.protocol) {
    case 'vless':
      return {
        tag: 'proxy',
        protocol: 'vless',
        settings: {
          vnext: [{ address: cfg.server, port: Number(cfg.port), users: [{ id: cfg.uuid, level: 8, encryption: cfg.extra?.encryption || 'none', flow: cfg.extra?.flow || undefined }] }],
        },
        streamSettings,
        mux,
      };
    case 'vmess':
      return {
        tag: 'proxy',
        protocol: 'vmess',
        settings: {
          vnext: [{ address: cfg.server, port: Number(cfg.port), users: [{ id: cfg.uuid, alterId: cfg.extra?.aid ?? 0, security: cfg.extra?.scy || 'auto', level: 8 }] }],
        },
        streamSettings,
        mux,
      };
    case 'trojan':
      return {
        tag: 'proxy',
        protocol: 'trojan',
        settings: { servers: [{ address: cfg.server, port: Number(cfg.port), password: cfg.password, level: 8 }] },
        streamSettings,
        mux,
      };
    case 'shadowsocks':
      return {
        tag: 'proxy',
        protocol: 'shadowsocks',
        settings: { servers: [{ address: cfg.server, port: Number(cfg.port), method: cfg.method, password: cfg.password, level: 8 }] },
        streamSettings,
        mux,
      };
    default:
      throw new Error('xray_export_unsupported_protocol');
  }
}

// `socksPort` is parameterized (unlike the bot's fixed-10808 version)
// because this script runs several of these concurrently, each needing
// its own local port. `logLevel` defaults to 'warning' (same as the bot's
// own export) but the test script passes 'debug' so dial/handshake
// failures against the actual remote server show up in the captured log
// instead of being silently swallowed.
function toXrayConfigJson(cfg, socksPort, logLevel = 'warning') {
  return {
    remarks: cfg.remark || `${cfg.protocol}-${cfg.server}`,
    log: { loglevel: logLevel },
    inbounds: [
      {
        tag: 'socks',
        port: socksPort,
        protocol: 'socks',
        listen: '127.0.0.1',
        settings: { auth: 'noauth', udp: true, userLevel: 8 },
        sniffing: { enabled: true, destOverride: ['http', 'tls', 'quic'], routeOnly: false },
      },
    ],
    outbounds: [
      buildProxyOutbound(cfg),
      { tag: 'direct', protocol: 'freedom', settings: { domainStrategy: 'UseIP' }, mux: { enabled: false, concurrency: 8, xudpConcurrency: 8, xudpProxyUDP443: '' } },
      { tag: 'block', protocol: 'blackhole', settings: { response: { type: 'http' } }, mux: { enabled: false, concurrency: 8, xudpConcurrency: 8, xudpProxyUDP443: '' } },
    ],
    dns: { servers: ['1.1.1.1'], queryStrategy: 'UseIPv4' },
    routing: { domainStrategy: 'AsIs', rules: [] },
  };
}

export {
  detectProtocol,
  parseConfig,
  serializeConfig,
  toXrayConfigJson,
  REGISTRY,
  ConfigParseError,
};
