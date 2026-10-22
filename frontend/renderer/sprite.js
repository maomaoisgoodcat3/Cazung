/* Cazung pixel-art sprite: 32x32 chibi composed from layers at runtime.
 * Works in the browser (window.CazungSprite) and in Node (require) for the tray icon. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.CazungSprite = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const W = 32, H = 32;

  const PALETTE = {
    k: '#2a1b33', // outline
    h: '#ff8ab8', // hair
    d: '#d9578f', // hair shadow
    H: '#ffd0e4', // hair highlight
    s: '#ffe5d4', // skin
    S: '#f4bfa6', // skin shadow
    e: '#3b2352', // eye dark
    i: '#8b5cf6', // iris
    I: '#c9b8ff', // iris light
    w: '#ffffff', // white
    b: '#ff9db3', // blush
    m: '#a8324f', // mouth
    M: '#ff7f9c', // mouth inside
    r: '#ff4d6d', // ribbon
    R: '#c9184a', // ribbon dark
    y: '#ffd166', // hair clip
    c: '#5b8def', // dress
    C: '#3d63b8', // dress shadow
    o: '#5a3550', // shoes
    z: '#ffffff', // effect white
    B: '#8ecdf7', // sweat / water
    g: '#d4d6e2', // prop light grey
    G: '#6e7088', // prop dark grey
    n: '#7fe0ff', // screen / glass glow
  };

  // '.' = transparent. Outline (k) is generated automatically around the silhouette.
  const BASE = [
    '................................', // 0
    '............hhhhhhhh............', // 1
    '..........hhhhHHhhhhhh..........', // 2
    '.........hhhHHhhhhhhhhh.........', // 3
    '....rr..hhHHhhhhhhhhhhhh..rr....', // 4
    '...rRrr.hHhhhhhhhhhhhhhh.rrRr...', // 5
    '...rrRrhhhhhhhhhhhhhhhhhhrRrr...', // 6
    '....rrhhhhhhhhhhhhhhhhhhhhrr....', // 7
    '..hhhdhhhhhhhhhhhhhhhhhyyhdhhh..', // 8
    '..hhhdhhhhdhhhhdhhhhdhhyyhdhhh..', // 9
    '..hhhdhhdhhdssdhhdssdhhdhhdhhh..', // 10
    '..hhhdhhsssssssssssssssshhdhhh..', // 11
    '..hhhdhhsssssssssssssssshhdhhh..', // 12
    '..hhhdhhsssssssssssssssshhdhhh..', // 13
    '..hhhdhhsssssssssssssssshhdhhh..', // 14
    '.hhhd.hhsssssssssssssssshh.dhhh.', // 15
    '.hhhd.hhhsssssssssssssshhh.dhhh.', // 16
    '.hhd..hhh.SssssssssssS.hhh..dhh.', // 17
    '.hhd..hh......ssss......hh..dhh.', // 18
    '.hhd......wwwwwrrwwwww......dhh.', // 19
    '.hhd....cccwwwrRRrwwwccc....dhh.', // 20
    '.hd.....cCccwwwrrwwwccCc.....dh.', // 21
    '..h.....cCcccccrrcccccCc.....h..', // 22
    '........ssCccccccccccCss........', // 23
    '.........ccCcccCCcccCcc.........', // 24
    '........ccCcccCccCcccCcc........', // 25
    '........CCCCCCCCCCCCCCCC........', // 26
    '............ss....ss............', // 27
    '............ss....ss............', // 28
    '............ww....ww............', // 29
    '...........ooo....ooo...........', // 30
    '................................', // 31
  ];

  // ---- face parts (eyes 3x4 at rows 11-14; left eye x=10, right eye x=19) ----
  const EYE_L = { x: 10, y: 11 }, EYE_R = { x: 19, y: 11 };
  const EYES = {
    open:     { L: ['eee', 'wii', 'iii', 'iIi'] },
    closed:   { L: ['sss', 'sss', 'eee', 'sss'] },
    happy:    { L: ['sss', 'ses', 'ese', 'sss'] },
    wide:     { L: ['eee', 'ewe', 'eie', 'eee'] },
    sad:      { L: ['see', 'eii', 'wii', 'iIi'], R: ['ees', 'iie', 'iiw', 'iIi'] },
    angry:    { L: ['ess', 'eee', 'wii', 'iIi'], R: ['sse', 'eee', 'iiw', 'iIi'] },
    half:     { L: ['sss', 'eee', 'wii', 'iIi'] },
    heart:    { L: ['rsr', 'rrr', 'rrr', 'srs'] },
    up:       { L: ['eee', 'iwi', 'iii', 'sss'] },
    sleep:    { L: ['sss', 'sss', 'ese', 'ses'] },
    wink:     { L: ['eee', 'wii', 'iii', 'iIi'], R: ['sss', 'ses', 'ese', 'sss'] },
  };
  // mouth 4x2 at x=14, y=15
  const MOUTH_POS = { x: 14, y: 15 };
  const MOUTHS = {
    smile:  ['mssm', 'smms'],
    small:  ['ssss', 'smms'],
    open:   ['smms', 'sMMs'],
    talk:   ['smms', 'smms'],
    o:      ['smms', 'smms'],
    frown:  ['ssss', 'mssm'],
    flat:   ['ssss', 'smms'],
    smirk:  ['ssss', 'ssmm'],
    wavy:   ['ssss', 'msms'],
    grin:   ['mmmm', 'sMMs'],
  };
  const BLUSH = [[9, 15], [10, 15], [21, 15], [22, 15]];

  // legs: rows 27-30 replaced as a block
  const LEGS = {
    stand: ['............ss....ss............',
            '............ss....ss............',
            '............ww....ww............',
            '...........ooo....ooo...........'],
    walkA: ['............ss....ss............',
            '............ww....ss............',
            '...........ooo....ww............',
            '..................ooo...........'],
    walkB: ['............ss....ss............',
            '............ss....ww............',
            '............ww....ooo...........',
            '...........ooo..................'],
    dangle:['............ss....ss............',
            '...........ss......ss...........',
            '...........ww......ww...........',
            '..........oo........oo..........'],
    sit:   ['................................', // rows 27-29 get covered by the lowered skirt
            '................................',
            '................................',
            '..........oooo....oooo..........'],
  };

  // arm overlays: list of [x, y, char]; '.' clears a pixel
  const ARMS = {
    down: [],
    wave: [ // right arm raised beside the head
      [22, 21, '.'], [23, 21, '.'], [22, 22, '.'], [23, 22, '.'], [22, 23, '.'], [23, 23, '.'],
      [24, 20, 'c'], [24, 19, 'c'], [25, 19, 'C'], [25, 18, 'c'],
      [25, 17, 's'], [26, 17, 's'], [25, 16, 's'], [26, 16, 's'],
    ],
  };
  ARMS.up = ARMS.wave.concat(ARMS.wave.map(([x, y, ch]) => [W - 1 - x, y, ch]));

  // effects (drawn on the 40x40 canvas, outside the 32x32 body)
  const EFFECTS = {
    z:      ['zzz', '..z', '.z.', 'z..', 'zzz'],
    heart:  ['.r.r.', 'rrrrr', 'rrrrr', '.rrr.', '..r..'],
    excl:   ['r', 'r', 'r', '.', 'r'],
    quest:  ['.zz.', 'z..z', '..z.', '....', '..z.'],
    dot:    ['z'],
    anger:  ['rr.rr', 'r...r', '.....', 'r...r', 'rr.rr'],
    note:   ['..zz', '..zz', '..z.', 'zzz.', 'zz..'],
    sweat:  ['.B.', 'BBB', 'BBB', '.B.'],
    sparkle:['.y.', 'yyy', '.y.'],
    tear:   ['B', 'B'],
    laptop: ['.GGGGGGGGGG.', '.GggggggggG.', '.GgggnngggG.', '.GggggggggG.', 'GGGGGGGGGGGG', '.gggggggggg.'],
    mag:    ['.GGG..', 'GnnnG.', 'GnwnG.', 'GnnnG.', '.GGG..', '....o.', '.....o'],
    mail:   ['wwwwwww', 'wrwwwrw', 'wwrwrww', 'wwwrwww', 'wwwwwww'],
    gear:   ['.G.G.', 'GGGGG', '.GwG.', 'GGGGG', '.G.G.'],
    gear2:  ['G.G.G', '.GGG.', 'GGwGG', '.GGG.', 'G.G.G'],
    book:   ['rrrrrr', 'rwwwwr', 'rweewr', 'rwwwwr', 'rrrrrr'],
    cloud:  ['..zzzz...', '.zzzzzzz.', 'zzzzzzzzz', '.zzzzzzz.', '...zz....', '.z.......'],
  };

  function grid(rows) {
    return rows.map((r, n) => {
      if (r.length !== W) throw new Error('sprite row ' + n + ' has width ' + r.length);
      return r.split('');
    });
  }

  function put(g, x, y, ch) {
    if (y >= 0 && y < g.length && x >= 0 && x < g[0].length) g[y][x] = ch;
  }

  function stamp(g, rows, ox, oy, mirror) {
    rows.forEach((row, dy) => {
      const chars = mirror ? row.split('').reverse() : row.split('');
      chars.forEach((ch, dx) => { if (ch !== '_') put(g, ox + dx, oy + dy, ch); });
    });
  }

  function outline(g) {
    const h = g.length, w = g[0].length;
    const out = g.map(r => r.slice());
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      if (g[y][x] !== '.') continue;
      const n = (yy, xx) => yy >= 0 && yy < h && xx >= 0 && xx < w && g[yy][xx] !== '.' && g[yy][xx] !== 'k';
      if (n(y - 1, x) || n(y + 1, x) || n(y, x - 1) || n(y, x + 1)) out[y][x] = 'k';
    }
    return out;
  }

  function shiftDown(g, lastRow, dy) {
    for (let y = lastRow; y >= 0; y--) for (let x = 0; x < W; x++) g[y][x] = y - dy >= 0 ? g[y - dy][x] : '.';
  }

  /** opts: {eyes, mouth, blush, legs, arms, bob, look(-1|0|1), sit} -> 2D char grid with outline */
  function compose(opts) {
    const o = Object.assign({ eyes: 'open', mouth: 'smile', blush: false, legs: 'stand', arms: 'down', bob: 0, look: 0, sit: false }, opts);
    const g = grid(BASE);
    const eye = EYES[o.eyes] || EYES.open;
    const look = Math.max(-1, Math.min(1, o.look | 0));
    stamp(g, eye.L, EYE_L.x + look, EYE_L.y, false);
    stamp(g, eye.R || eye.L, EYE_R.x + look, EYE_R.y, false);
    stamp(g, MOUTHS[o.mouth] || MOUTHS.smile, MOUTH_POS.x, MOUTH_POS.y, false);
    if (o.blush) BLUSH.forEach(([x, y]) => put(g, x, y, 'b'));
    stamp(g, LEGS[o.sit ? 'sit' : o.legs] || LEGS.stand, 0, 27, false);
    (ARMS[o.arms] || []).forEach(([x, y, ch]) => put(g, x, y, ch));
    if (o.sit) shiftDown(g, 29, 3);             // sit on the taskbar: body drops, shoes stick out
    if (o.bob > 0) shiftDown(g, o.sit ? 29 : 26, o.bob); // breathe / squash
    return outline(g);
  }

  function effectGrid(name) {
    const rows = EFFECTS[name];
    const g = rows.map(r => r.split(''));
    // pad by 1 so the outline fits
    const w = g[0].length + 2;
    const padded = [Array(w).fill('.')].concat(g.map(r => ['.'].concat(r, ['.'])), [Array(w).fill('.')]);
    return outline(padded);
  }

  function hexToRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  /** draw a char grid on a 2D canvas context at logical (ox, oy) with scale */
  function draw(ctx, g, ox, oy, scale, flip) {
    const w = g[0].length;
    for (let y = 0; y < g.length; y++) for (let x = 0; x < w; x++) {
      const ch = g[y][x];
      if (ch === '.') continue;
      ctx.fillStyle = PALETTE[ch] || '#ff00ff';
      const px = flip ? (w - 1 - x) : x;
      ctx.fillRect((ox + px) * scale, (oy + y) * scale, scale, scale);
    }
  }

  /** RGBA buffer (for PNG preview / tray icon in Node) */
  function toRGBA(g, scale) {
    scale = scale || 1;
    const h = g.length, w = g[0].length;
    const buf = new Uint8Array(w * scale * h * scale * 4);
    for (let y = 0; y < h * scale; y++) for (let x = 0; x < w * scale; x++) {
      const ch = g[Math.floor(y / scale)][Math.floor(x / scale)];
      const i = (y * w * scale + x) * 4;
      if (ch === '.') continue;
      const [r, gg, b] = hexToRgb(PALETTE[ch] || '#ff00ff');
      buf[i] = r; buf[i + 1] = gg; buf[i + 2] = b; buf[i + 3] = 255;
    }
    return { width: w * scale, height: h * scale, data: buf };
  }

  // emotion -> expression
  const EXPRESSIONS = {
    neutral:   { eyes: 'open',  mouth: 'smile' },
    happy:     { eyes: 'happy', mouth: 'grin',  blush: true, effect: 'note' },
    sad:       { eyes: 'sad',   mouth: 'frown', effect: 'tear' },
    surprised: { eyes: 'wide',  mouth: 'o',     effect: 'excl' },
    angry:     { eyes: 'angry', mouth: 'frown', effect: 'anger' },
    shy:       { eyes: 'half',  mouth: 'wavy',  blush: true, effect: 'sweat' },
    love:      { eyes: 'heart', mouth: 'smile', blush: true, effect: 'heart' },
    thinking:  { eyes: 'up',    mouth: 'flat',  effect: 'quest' },
    sleepy:    { eyes: 'half',  mouth: 'small', effect: 'z' },
    smug:      { eyes: 'half',  mouth: 'smirk', effect: 'sparkle' },
  };

  return { W, H, PALETTE, EYES, MOUTHS, LEGS, ARMS, EFFECTS, EXPRESSIONS, compose, effectGrid, draw, toRGBA, outline };
});
