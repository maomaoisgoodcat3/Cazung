/* Cazung the desktop pet: animation state machine, rendering and mouse interaction. */
(function () {
  'use strict';
  const S = window.CazungSprite;
  const CW = 40, CH = 40;     // logical canvas (pixels)
  const SX = 4, SY = 8;       // where the 32x32 body sits inside it
  const PHYSICAL = new Set(['drag', 'fall', 'land']);
  // modes that end by themselves (ms)
  const TIMED = { land: 380, wave: 1800, petted: 1800, jump: 650, dance: 3600, stretch: 1700, yawn: 1900,
                  look: 2400, hum: 3600, alert: 4000 };
  const IDLE_LIKE = new Set(['idle', 'sit']);

  class Pet {
    constructor(canvas, opts = {}) {
      this.canvas = canvas;
      this.scale = opts.scale || 4;
      this.desktop = opts.desktop || null;
      canvas.width = CW * this.scale;
      canvas.height = CH * this.scale;
      this.ctx = canvas.getContext('2d', { willReadFrequently: true });
      this.ctx.imageSmoothingEnabled = false;
      this.cache = new Map();
      this.fxCache = new Map();
      this.handlers = {};
      this.mode = 'idle';
      this.modeSince = performance.now();
      this.emotion = null;
      this.emotionUntil = 0;
      this.nextBlink = 0;
      this.blinkUntil = 0;
      this.walkDir = 1;
      this.walkUntil = 0;
      this.sitUntil = 0;
      this.lastMove = 0;
      this.nextAction = performance.now() + 8000;
      this.lookDir = 0;
      this.lookUntil = 0;
      this.lastInteraction = Date.now();
      this.wander = true;
      this.sleepAfterMs = 5 * 60 * 1000;
      this.canIdleActs = () => true;
      this._bindInput();
      requestAnimationFrame(t => this._loop(t));
      setInterval(() => this._behave(), 1000);
    }

    on(evt, fn) { this.handlers[evt] = fn; }
    emit(evt, ...a) { if (this.handlers[evt]) this.handlers[evt](...a); }

    get physical() { return PHYSICAL.has(this.mode); }
    get idleLike() { return IDLE_LIKE.has(this.mode); }

    setMode(mode) {
      if (this.mode === mode) return;
      if (this.physical && !['land', 'idle', 'fall', 'jump'].includes(mode)) return; // don't interrupt being carried
      this.mode = mode;
      this.modeSince = performance.now();
    }

    /** chat/LLM driven modes; ignored while she is being dragged */
    act(mode) { if (!this.physical) this.setMode(mode); }

    setEmotion(emo, ms = 7000) {
      this.emotion = S.EXPRESSIONS[emo] ? emo : 'neutral';
      this.emotionUntil = performance.now() + ms;
    }

    /** a little body-language reaction to a finished reply */
    react(emo) {
      if (this.physical) return;
      if (emo === 'happy' || emo === 'love') this.setMode(Math.random() < 0.5 ? 'jump' : 'hum');
      else if (emo === 'surprised') this.setMode('alert');
      else if (emo === 'smug') this.setMode('look');
      else this.setMode('idle');
    }

    /** eyes follow the cursor when it is near */
    lookAt(dx) {
      this.lookDir = Math.abs(dx) < 25 ? 0 : Math.sign(dx);
      this.lookUntil = performance.now() + 1200;
    }

    touch() {
      this.lastInteraction = Date.now();
      if (this.mode === 'sleep' || this.mode === 'yawn') {
        this.sleepAfterYawn = false;
        this.setMode('stretch');
        this.setEmotion('surprised', 1400);
      }
    }

    // ------------------------------------------------------------ loop
    _loop(t) {
      this._update(t);
      this._draw(t);
      requestAnimationFrame(tt => this._loop(tt));
    }

    _update(t) {
      if (t > this.nextBlink) {
        this.blinkUntil = t + 130;
        this.nextBlink = t + (Math.random() < 0.2 ? 280 : 1800 + Math.random() * 4500);
      }
      const age = t - this.modeSince;
      if (this.mode === 'walk') {
        if (t > this.walkUntil || !this.desktop || !this.canIdleActs()) this.setMode('idle');
        else if (t - this.lastMove > 33 && !this.moving) {
          this.moving = true;
          this.lastMove = t;
          this.desktop.moveBy(this.walkDir * 2).then(r => {
            this.moving = false;
            if (r && ((r.atLeft && this.walkDir < 0) || (r.atRight && this.walkDir > 0))) this.walkDir = -this.walkDir;
          }).catch(() => { this.moving = false; });
        }
      }
      if (this.mode === 'sit' && t > this.sitUntil) this.setMode('stretch');
      const limit = TIMED[this.mode];
      if (limit && age > limit) {
        if (this.mode === 'yawn' && this.sleepAfterYawn) { this.sleepAfterYawn = false; this.setMode('sleep'); }
        else if (this.mode === 'jump') { this.mode = 'land'; this.modeSince = t; }
        else this.setMode('idle');
      }
    }

    _behave() {
      if (!this.idleLike || !this.canIdleActs()) return;
      const now = performance.now();
      if (Date.now() - this.lastInteraction > this.sleepAfterMs) {
        this.sleepAfterYawn = true;
        this.setMode('yawn');
        return;
      }
      if (this.mode === 'sit' || now < this.nextAction) return;
      this.nextAction = now + 6000 + Math.random() * 14000;
      const night = new Date().getHours() >= 23 || new Date().getHours() < 5;
      const choices = [
        ['walk', this.wander && this.desktop ? 34 : 0], ['look', 20], ['sit', 12], ['stretch', 7],
        ['hum', 10], ['dance', 4], ['yawn', night ? 12 : 3], ['nothing', 20],
      ];
      let r = Math.random() * choices.reduce((a, [, w]) => a + w, 0);
      const pick = choices.find(([, w]) => (r -= w) < 0)[0];
      if (pick === 'walk') {
        this.walkDir = Math.random() < 0.5 ? -1 : 1;
        this.walkUntil = now + 1500 + Math.random() * 4500;
        this.setMode('walk');
      } else if (pick === 'sit') {
        this.sitUntil = now + 15000 + Math.random() * 30000;
        this.setMode('sit');
      } else if (pick !== 'nothing') {
        this.setMode(pick);
      }
    }

    // ----------------------------------------------------------- frame
    _frame(t) {
      const o = { eyes: 'open', mouth: 'smile', blush: false, legs: 'stand', arms: 'down', bob: 0, look: 0, sit: false };
      let fx = null, prop = null, ox = 0, oy = 0;
      const emoActive = this.emotion && t < this.emotionUntil;
      if (emoActive) {
        const e = S.EXPRESSIONS[this.emotion];
        o.eyes = e.eyes; o.mouth = e.mouth; o.blush = !!e.blush; fx = e.effect || null;
      }
      if (t < this.lookUntil) o.look = this.lookDir;
      const tick = ms => Math.floor(t / ms);
      const age = t - this.modeSince;
      switch (this.mode) {
        case 'idle': o.bob = tick(650) % 2; break;
        case 'sit': o.sit = true; o.bob = tick(900) % 2; break;
        case 'walk': {
          const ph = tick(170) % 4;
          o.legs = ['walkA', 'stand', 'walkB', 'stand'][ph];
          o.bob = ph % 2;
          o.look = this.walkDir;
          break;
        }
        case 'think': o.eyes = 'up'; o.mouth = 'flat'; o.look = 1; o.bob = tick(900) % 2; prop = 'cloud'; break;
        case 'talk':
          o.mouth = ['open', 'talk', 'small'][tick(120) % 3];
          if (!emoActive) o.eyes = 'open';
          o.bob = tick(420) % 2;
          break;
        case 'work':
          o.eyes = tick(1400) % 5 === 0 ? 'open' : 'half'; o.mouth = 'small'; o.bob = tick(180) % 2; prop = 'laptop';
          break;
        case 'search':
          o.arms = 'wave'; o.eyes = 'wide'; o.mouth = 'o'; o.look = tick(600) % 2 ? 1 : -1; prop = 'mag'; break;
        case 'mail': o.arms = 'wave'; o.eyes = 'happy'; o.mouth = 'grin'; prop = 'mail'; break;
        case 'scan': o.eyes = 'up'; o.mouth = 'flat'; o.look = tick(700) % 2 ? 1 : -1; prop = 'gears'; break;
        case 'sleep': o.eyes = 'sleep'; o.mouth = 'small'; o.blush = false; o.sit = true; o.bob = tick(1300) % 2; fx = 'zzz'; break;
        case 'drag': case 'fall':
          o.eyes = 'wide'; o.mouth = 'o'; o.legs = 'dangle'; o.arms = 'up'; fx = 'sweat';
          ox = tick(110) % 2 ? 1 : -1;
          break;
        case 'land': o.bob = 1; o.eyes = 'closed'; o.mouth = 'flat'; fx = null; break;
        case 'jump': {
          const p = Math.min(1, age / TIMED.jump);
          oy = -Math.round(7 * Math.sin(Math.PI * p));
          if (p > 0.15 && p < 0.85) { o.legs = 'dangle'; o.arms = 'up'; }
          o.eyes = 'happy'; o.mouth = 'grin'; o.blush = true;
          break;
        }
        case 'dance': {
          const ph = tick(220) % 4;
          Object.assign(o, [{ arms: 'wave', look: -1 }, { arms: 'up', bob: 1 }, { arms: 'wave', look: 1 }, { arms: 'down', bob: 1 }][ph]);
          ox = [-1, 0, 1, 0][ph];
          o.eyes = 'happy'; o.mouth = 'grin'; o.blush = true; fx = 'note';
          break;
        }
        case 'stretch':
          o.arms = 'up'; o.eyes = 'closed'; o.mouth = age < 900 ? 'o' : 'smile';
          oy = age > 300 && age < 1200 ? -1 : 0;
          break;
        case 'yawn': o.eyes = 'closed'; o.mouth = age < 1300 ? 'open' : 'small'; o.bob = age > 700 ? 1 : 0; fx = null; break;
        case 'look': o.look = age < 800 ? -1 : age < 1600 ? 1 : 0; if (age > 1600 && age < 1900) o.eyes = 'wide'; break;
        case 'hum': o.eyes = 'happy'; o.mouth = tick(400) % 2 ? 'smile' : 'small'; o.bob = tick(300) % 2; fx = 'note'; break;
        case 'wave':
          o.arms = tick(230) % 2 ? 'wave' : 'down'; o.eyes = 'happy'; o.mouth = 'grin'; o.blush = true; fx = 'note'; break;
        case 'petted': o.eyes = 'happy'; o.mouth = 'grin'; o.blush = true; o.bob = tick(240) % 2; fx = 'heart'; break;
        case 'alert': o.eyes = 'wide'; o.mouth = 'o'; o.arms = tick(200) % 2 ? 'wave' : 'down'; fx = 'excl'; break;
      }
      if (o.eyes === 'open' && t < this.blinkUntil) o.eyes = 'closed';
      return { o, fx, prop, ox, oy };
    }

    _body(o) {
      const key = JSON.stringify(o);
      let c = this.cache.get(key);
      if (!c) {
        c = document.createElement('canvas');
        c.width = S.W * this.scale; c.height = S.H * this.scale;
        S.draw(c.getContext('2d'), S.compose(o), 0, 0, this.scale);
        if (this.cache.size > 300) this.cache.clear();
        this.cache.set(key, c);
      }
      return c;
    }

    _fx(name, x, y) {
      let g = this.fxCache.get(name);
      if (!g) { g = S.effectGrid(name); this.fxCache.set(name, g); }
      S.draw(this.ctx, g, x, y, this.scale);
    }

    _px(x, y, color) {
      this.ctx.fillStyle = S.PALETTE[color] || color;
      this.ctx.fillRect(x * this.scale, y * this.scale, this.scale, this.scale);
    }

    _draw(t) {
      const { o, fx, prop, ox, oy } = this._frame(t);
      const ctx = this.ctx, sc = this.scale;
      const age = t - this.modeSince;
      ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      ctx.drawImage(this._body(o), (SX + ox) * sc, (SY + oy) * sc);
      const bounce = Math.floor(t / 320) % 2;

      switch (prop) { // props held / shown while she works on something
        case 'laptop': this._fx('laptop', 13 + ox, 29 + oy + (o.sit ? 3 : 0)); break;
        case 'mag': this._fx('mag', 26, 15 + bounce); break;
        case 'mail': {
          const q = (age / 70) % 18;
          this._fx('mail', 28 + Math.floor(q / 3), 13 - Math.floor(q / 2));
          break;
        }
        case 'gears': {
          const f = Math.floor(t / 250) % 2;
          this._fx(f ? 'gear' : 'gear2', 31, 1);
          this._fx(f ? 'gear2' : 'gear', 26, 4);
          break;
        }
        case 'cloud': {
          this._fx('cloud', 28, 0);
          const n = Math.floor(t / 330) % 4;
          for (let i = 0; i < n; i++) this._px(31 + i * 2, 3, 'e');
          break;
        }
      }

      if (prop === 'cloud' || prop === 'gears' || prop === 'mail') return; // keep the top-right corner clean
      switch (fx) {
        case null: break;
        case 'zzz':
          for (let k = 0; k < 2; k++) {
            const q = (Math.floor(t / 110) + k * 12) % 24;
            this._fx('z', 29 + Math.floor(q / 6), 8 - Math.floor(q / 3));
          }
          break;
        case 'tear': {
          const q = Math.floor(t / 110) % 8;
          this._fx('tear', 13 + ox, 22 + q + oy);
          break;
        }
        case 'sweat': this._fx('sweat', 31, 9 + bounce); break;
        default: this._fx(fx, 31, 1 + bounce);
      }
    }

    // ----------------------------------------------------------- input
    _bindInput() {
      const c = this.canvas;
      c.addEventListener('pointerdown', e => {
        if (e.button !== 0) return;
        this.down = { x: e.screenX, y: e.screenY };
        this.dragging = false;
        try { c.setPointerCapture(e.pointerId); } catch (err) { /* synthetic event */ }
      });
      c.addEventListener('pointermove', e => {
        if (this.down) {
          if (!this.dragging && this.desktop && Math.hypot(e.screenX - this.down.x, e.screenY - this.down.y) > 6) {
            this.dragging = true;
            this.touch();
            this.mode = 'drag'; this.modeSince = performance.now();
            c.classList.add('dragging');
            this.desktop.dragStart();
            this.emit('dragstart');
          }
          return;
        }
        this._stroke(e);
      });
      const end = () => {
        if (!this.down) return;
        const wasDrag = this.dragging;
        this.down = null; this.dragging = false;
        c.classList.remove('dragging');
        if (wasDrag) { this.desktop.dragEnd(); this.mode = 'fall'; this.modeSince = performance.now(); return; }
        this.touch();
        // single click = chat, double click = little dance
        if (this.clickTimer) {
          clearTimeout(this.clickTimer);
          this.clickTimer = null;
          this.setMode(Math.random() < 0.5 ? 'dance' : 'jump');
          this.emit('dblclick');
        } else {
          this.clickTimer = setTimeout(() => { this.clickTimer = null; this.emit('click'); }, 230);
        }
      };
      c.addEventListener('pointerup', end);
      c.addEventListener('pointercancel', end);
      c.addEventListener('contextmenu', e => { e.preventDefault(); this.emit('contextmenu'); });
    }

    landed() { this.mode = 'land'; this.modeSince = performance.now(); }

    setScale(scale) {
      scale = Math.max(2, Math.min(8, Math.round(scale)));
      if (scale === this.scale) return;
      this.scale = scale;
      this.canvas.width = CW * scale;
      this.canvas.height = CH * scale;
      this.ctx.imageSmoothingEnabled = false;
      this.cache.clear();
    }

    /** true if the mouse is over a visible pixel (with a little tolerance) */
    hitTest(clientX, clientY) {
      const r = this.canvas.getBoundingClientRect();
      const x = Math.floor(clientX - r.left), y = Math.floor(clientY - r.top);
      const pad = this.scale * 2;
      if (x < -pad || y < -pad || x > r.width + pad || y > r.height + pad) return false;
      const x0 = Math.max(0, x - pad), y0 = Math.max(0, y - pad);
      const w = Math.min(this.canvas.width - x0, pad * 2), h = Math.min(this.canvas.height - y0, pad * 2);
      if (w <= 0 || h <= 0) return false;
      const data = this.ctx.getImageData(x0, y0, w, h).data;
      for (let i = 3; i < data.length; i += 16) if (data[i] > 0) return true;
      return false;
    }

    /** rubbing the mouse left-right over her head = head pats */
    _stroke(e) {
      const rect = this.canvas.getBoundingClientRect();
      const ly = (e.clientY - rect.top) / this.scale;
      if (ly > SY + 17 || !e.movementX) return;
      const now = performance.now(), dir = Math.sign(e.movementX);
      if (this.strokeDir && dir !== this.strokeDir) {
        this.strokes = now - (this.strokeT || 0) < 500 ? (this.strokes || 0) + 1 : 1;
        this.strokeT = now;
      }
      this.strokeDir = dir;
      if (this.strokes >= 5 && !this.physical) {
        this.strokes = 0;
        this.touch();
        this.setMode('petted');
        this.emit('petted');
      }
    }
  }

  window.Pet = Pet;
})();
