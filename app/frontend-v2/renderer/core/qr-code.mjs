const QR_L_PARAMS = [
  null,
  { version: 1, dataCodewords: 19, ecCodewords: 7, capacity: 17 },
  { version: 2, dataCodewords: 34, ecCodewords: 10, capacity: 32 },
  { version: 3, dataCodewords: 55, ecCodewords: 15, capacity: 53 },
  { version: 4, dataCodewords: 80, ecCodewords: 20, capacity: 78 },
  { version: 5, dataCodewords: 108, ecCodewords: 26, capacity: 106 },
];

let gfExp = null;
let gfLog = null;

function initGalois() {
  if (gfExp && gfLog) return;
  gfExp = new Array(512).fill(0);
  gfLog = new Array(256).fill(0);
  let value = 1;
  for (let i = 0; i < 255; i += 1) {
    gfExp[i] = value;
    gfLog[value] = i;
    value <<= 1;
    if (value & 0x100) value ^= 0x11d;
  }
  for (let i = 255; i < 512; i += 1) gfExp[i] = gfExp[i - 255];
}

function gfMul(a, b) {
  if (!a || !b) return 0;
  initGalois();
  return gfExp[gfLog[a] + gfLog[b]];
}

function polyMul(a, b) {
  const result = new Array(a.length + b.length - 1).fill(0);
  for (let i = 0; i < a.length; i += 1) {
    for (let j = 0; j < b.length; j += 1) {
      result[i + j] ^= gfMul(a[i], b[j]);
    }
  }
  return result;
}

function rsGenerator(degree) {
  initGalois();
  let poly = [1];
  for (let i = 0; i < degree; i += 1) {
    poly = polyMul(poly, [1, gfExp[i]]);
  }
  return poly;
}

function rsRemainder(data, degree) {
  const generator = rsGenerator(degree);
  let result = new Array(degree).fill(0);
  for (const byte of data) {
    const factor = byte ^ result[0];
    result = result.slice(1);
    result.push(0);
    for (let i = 0; i < degree; i += 1) {
      result[i] ^= gfMul(generator[i + 1], factor);
    }
  }
  return result;
}

function appendBits(bits, value, count) {
  for (let i = count - 1; i >= 0; i -= 1) bits.push((value >>> i) & 1);
}

function chooseParams(bytes) {
  return QR_L_PARAMS.find((params) => params && bytes.length <= params.capacity) || null;
}

function dataCodewords(bytes, params) {
  const bits = [];
  appendBits(bits, 0b0100, 4);
  appendBits(bits, bytes.length, 8);
  for (const byte of bytes) appendBits(bits, byte, 8);

  const capacityBits = params.dataCodewords * 8;
  const terminator = Math.min(4, capacityBits - bits.length);
  appendBits(bits, 0, terminator);
  while (bits.length % 8) bits.push(0);

  const codewords = [];
  for (let i = 0; i < bits.length; i += 8) {
    let value = 0;
    for (let j = 0; j < 8; j += 1) value = (value << 1) | bits[i + j];
    codewords.push(value);
  }

  for (let pad = 0; codewords.length < params.dataCodewords; pad += 1) {
    codewords.push(pad % 2 ? 0x11 : 0xec);
  }
  return codewords;
}

function makeMatrix(size) {
  return {
    modules: Array.from({ length: size }, () => new Array(size).fill(false)),
    reserved: Array.from({ length: size }, () => new Array(size).fill(false)),
  };
}

function inBounds(size, x, y) {
  return x >= 0 && y >= 0 && x < size && y < size;
}

function setModule(qr, x, y, value, reserve = true) {
  if (!inBounds(qr.modules.length, x, y)) return;
  qr.modules[y][x] = Boolean(value);
  if (reserve) qr.reserved[y][x] = true;
}

function drawFinder(qr, x0, y0) {
  for (let y = -1; y <= 7; y += 1) {
    for (let x = -1; x <= 7; x += 1) {
      const xx = x0 + x;
      const yy = y0 + y;
      if (!inBounds(qr.modules.length, xx, yy)) continue;
      const dark = x >= 0 && x <= 6 && y >= 0 && y <= 6
        && (x === 0 || x === 6 || y === 0 || y === 6 || (x >= 2 && x <= 4 && y >= 2 && y <= 4));
      setModule(qr, xx, yy, dark, true);
    }
  }
}

function drawAlignment(qr, cx, cy) {
  for (let y = -2; y <= 2; y += 1) {
    for (let x = -2; x <= 2; x += 1) {
      const edge = Math.max(Math.abs(x), Math.abs(y)) === 2;
      setModule(qr, cx + x, cy + y, edge || (x === 0 && y === 0), true);
    }
  }
}

function reserveFormat(qr) {
  const size = qr.modules.length;
  for (let i = 0; i <= 5; i += 1) setModule(qr, 8, i, false, true);
  setModule(qr, 8, 7, false, true);
  setModule(qr, 8, 8, false, true);
  setModule(qr, 7, 8, false, true);
  for (let i = 9; i < 15; i += 1) setModule(qr, 14 - i, 8, false, true);
  for (let i = 0; i < 8; i += 1) setModule(qr, size - 1 - i, 8, false, true);
  for (let i = 8; i < 15; i += 1) setModule(qr, 8, size - 15 + i, false, true);
}

function drawFunctionPatterns(qr, version) {
  const size = qr.modules.length;
  drawFinder(qr, 0, 0);
  drawFinder(qr, size - 7, 0);
  drawFinder(qr, 0, size - 7);

  for (let i = 8; i < size - 8; i += 1) {
    const dark = i % 2 === 0;
    setModule(qr, i, 6, dark, true);
    setModule(qr, 6, i, dark, true);
  }

  if (version > 1) drawAlignment(qr, version * 4 + 10, version * 4 + 10);
  setModule(qr, 8, size - 8, true, true);
  reserveFormat(qr);
}

function placeData(qr, codewords) {
  const size = qr.modules.length;
  const bits = [];
  for (const codeword of codewords) appendBits(bits, codeword, 8);
  let bitIndex = 0;
  let upward = true;

  for (let col = size - 1; col > 0; col -= 2) {
    if (col === 6) col -= 1;
    for (let offset = 0; offset < size; offset += 1) {
      const y = upward ? size - 1 - offset : offset;
      for (let c = 0; c < 2; c += 1) {
        const x = col - c;
        if (qr.reserved[y][x]) continue;
        qr.modules[y][x] = Boolean(bits[bitIndex] || 0);
        bitIndex += 1;
      }
    }
    upward = !upward;
  }
}

function maskCondition(mask, x, y) {
  switch (mask) {
    case 0: return (x + y) % 2 === 0;
    case 1: return y % 2 === 0;
    case 2: return x % 3 === 0;
    case 3: return (x + y) % 3 === 0;
    case 4: return (Math.floor(y / 2) + Math.floor(x / 3)) % 2 === 0;
    case 5: return ((x * y) % 2) + ((x * y) % 3) === 0;
    case 6: return (((x * y) % 2) + ((x * y) % 3)) % 2 === 0;
    case 7: return (((x + y) % 2) + ((x * y) % 3)) % 2 === 0;
    default: return false;
  }
}

function cloneModules(modules) {
  return modules.map((row) => row.slice());
}

function applyMask(modules, reserved, mask) {
  const result = cloneModules(modules);
  for (let y = 0; y < result.length; y += 1) {
    for (let x = 0; x < result.length; x += 1) {
      if (!reserved[y][x] && maskCondition(mask, x, y)) result[y][x] = !result[y][x];
    }
  }
  return result;
}

function linePenalty(line) {
  let penalty = 0;
  let runColor = line[0];
  let runLength = 1;
  for (let i = 1; i < line.length; i += 1) {
    if (line[i] === runColor) {
      runLength += 1;
    } else {
      if (runLength >= 5) penalty += 3 + runLength - 5;
      runColor = line[i];
      runLength = 1;
    }
  }
  if (runLength >= 5) penalty += 3 + runLength - 5;
  return penalty;
}

function finderPenalty(line) {
  const a = "10111010000";
  const b = "00001011101";
  const text = line.map((bit) => (bit ? "1" : "0")).join("");
  let penalty = 0;
  for (let i = 0; i <= text.length - 11; i += 1) {
    const chunk = text.slice(i, i + 11);
    if (chunk === a || chunk === b) penalty += 40;
  }
  return penalty;
}

function penaltyScore(modules) {
  const size = modules.length;
  let penalty = 0;
  let dark = 0;

  for (let y = 0; y < size; y += 1) {
    penalty += linePenalty(modules[y]);
    penalty += finderPenalty(modules[y]);
    for (let x = 0; x < size; x += 1) if (modules[y][x]) dark += 1;
  }

  for (let x = 0; x < size; x += 1) {
    const column = modules.map((row) => row[x]);
    penalty += linePenalty(column);
    penalty += finderPenalty(column);
  }

  for (let y = 0; y < size - 1; y += 1) {
    for (let x = 0; x < size - 1; x += 1) {
      const color = modules[y][x];
      if (modules[y][x + 1] === color && modules[y + 1][x] === color && modules[y + 1][x + 1] === color) {
        penalty += 3;
      }
    }
  }

  const total = size * size;
  penalty += Math.floor(Math.abs((dark * 100) / total - 50) / 5) * 10;
  return penalty;
}

function formatBits(mask) {
  const ecLevelL = 0b01;
  const data = (ecLevelL << 3) | mask;
  let bits = data << 10;
  for (let i = 14; i >= 10; i -= 1) {
    if ((bits >>> i) & 1) bits ^= 0x537 << (i - 10);
  }
  return ((data << 10) | bits) ^ 0x5412;
}

function drawFormat(modules, mask) {
  const size = modules.length;
  const bits = formatBits(mask);
  const set = (x, y, index) => {
    modules[y][x] = Boolean((bits >>> index) & 1);
  };

  for (let i = 0; i <= 5; i += 1) set(8, i, i);
  set(8, 7, 6);
  set(8, 8, 7);
  set(7, 8, 8);
  for (let i = 9; i < 15; i += 1) set(14 - i, 8, i);
  for (let i = 0; i < 8; i += 1) set(size - 1 - i, 8, i);
  for (let i = 8; i < 15; i += 1) set(8, size - 15 + i, i);
  modules[size - 8][8] = true;
}

function makeQrModules(text) {
  const bytes = Array.from(new TextEncoder().encode(String(text)));
  const params = chooseParams(bytes);
  if (!params) return null;

  const data = dataCodewords(bytes, params);
  const codewords = data.concat(rsRemainder(data, params.ecCodewords));
  const size = params.version * 4 + 17;
  const qr = makeMatrix(size);
  drawFunctionPatterns(qr, params.version);
  placeData(qr, codewords);

  let bestMask = 0;
  let bestModules = null;
  let bestPenalty = Infinity;
  for (let mask = 0; mask < 8; mask += 1) {
    const candidate = applyMask(qr.modules, qr.reserved, mask);
    drawFormat(candidate, mask);
    const score = penaltyScore(candidate);
    if (score < bestPenalty) {
      bestPenalty = score;
      bestMask = mask;
      bestModules = candidate;
    }
  }

  drawFormat(bestModules, bestMask);
  return bestModules;
}

export function qrTextToSvgDataUrl(text) {
  const modules = makeQrModules(text);
  if (!modules) return "";
  const size = modules.length;
  const quiet = 4;
  const outer = size + quiet * 2;
  const paths = [];

  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      if (modules[y][x]) paths.push(`M${x + quiet},${y + quiet}h1v1h-1z`);
    }
  }

  const svg = [
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${outer} ${outer}" shape-rendering="crispEdges">`,
    `<path fill="#fff" d="M0 0h${outer}v${outer}H0z"/>`,
    `<path fill="#111" d="${paths.join("")}"/>`,
    "</svg>",
  ].join("");
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
