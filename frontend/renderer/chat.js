/* Chat panel: WebSocket client, smooth streaming, live thinking, tool chips, confirmations, memory viewer. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const md = s => window.Markdown.render(s);
  const esc = s => window.Markdown.esc(String(s ?? ''));

  const KIND_LABEL = {
    instruction: 'luật', fact: 'sự thật', preference: 'sở thích', event: 'sự kiện',
    episode: 'kỷ niệm', note: 'ghi chú', lesson: 'bài học', skill: 'kỹ năng',
  };
  const TABS = {
    profile: 'instruction,fact,preference',
    events: 'event,episode,note',
    lesson: 'lesson',
    skill: 'skill',
  };
  const TOOL_ICONS = [
    [/__/, '🔌'], [/email/, '✉️'], [/search_code|file|tree|outline|python|shell/, '💻'],
    [/search|fetch|weather|open_url/, '🔎'], [/process|system|network|startup|installed|scan/, '🖥️'],
    [/remember|recall|forget|history|skill/, '🧠'], [/reminder/, '⏰'],
  ];
  const toolIcon = name => (TOOL_ICONS.find(([rx]) => rx.test(name)) || [0, '🔧'])[1];

  /** Reveals streamed text at an adaptive, smooth speed: never freezes, never lags far behind. */
  class Typewriter {
    constructor(render, speed = 1) {
      this.render = render;
      this.speed = speed;
      this.target = '';
      this.shown = 0;
      this.running = false;
      this.waiters = [];
    }
    push(text) { this.target += text; this._kick(); }
    set(text) {
      this.target = text;
      this.shown = Math.min(this.shown, text.length);
      this.render(this.target.slice(0, Math.floor(this.shown)));
      this._kick();
    }
    finishNow() { this.shown = this.target.length; this.render(this.target); this._resolve(); }
    idle() {
      if (!this.running && this.shown >= this.target.length) return Promise.resolve();
      return new Promise(r => this.waiters.push(r));
    }
    _resolve() { const w = this.waiters; this.waiters = []; w.forEach(r => r()); }
    _kick() {
      if (this.running) return;
      this.running = true;
      this.last = performance.now();
      this.lastRender = 0;
      requestAnimationFrame(ts => this._step(ts));
    }
    _step(ts) {
      const dt = Math.min(0.1, Math.max(0, (ts - this.last) / 1000));
      this.last = ts;
      const backlog = this.target.length - this.shown;
      if (backlog <= 0) { this.running = false; this._resolve(); return; }
      // ~40 chars/s when caught up, speeding up smoothly when the model is ahead
      const rate = (40 + backlog * 6) * this.speed;
      this.shown = Math.min(this.target.length, this.shown + Math.max(rate * dt, 0.35));
      let idx = Math.floor(this.shown);
      const code = this.target.charCodeAt(idx - 1);
      if (code >= 0xd800 && code <= 0xdbff) idx++; // never split an emoji
      if (idx >= this.target.length || this.target.length < 4000 || ts - this.lastRender > 50) {
        this.render(this.target.slice(0, idx));
        this.lastRender = ts;
      }
      requestAnimationFrame(t => this._step(t));
    }
  }

  class Chat {
    constructor() {
      this.panel = $('chat');
      this.list = $('messages');
      this.input = $('input');
      this.sendBtn = $('send');
      this.memView = $('memory-view');
      this.handlers = {};
      this.ws = null;
      this.backend = null;
      this.busy = false;
      this.cur = null;
      this.think = null;
      this.turnBubbles = [];
      this.queue = Promise.resolve();
      this.loaded = false;
      this.retryMs = 1000;
      this.tab = 'profile';
      this._bindUI();
    }

    on(evt, fn) { this.handlers[evt] = fn; }
    emit(evt, ...a) { if (this.handlers[evt]) this.handlers[evt](...a); }
    get isOpen() { return !this.panel.classList.contains('hidden'); }

    open() {
      this.panel.classList.remove('hidden');
      this._scroll(true);
      setTimeout(() => this.input.focus(), 30);
      this.emit('open');
    }
    close() { this.panel.classList.add('hidden'); this.emit('close'); }
    toggle() { this.isOpen ? this.close() : this.open(); }

    // ------------------------------------------------------- connection
    connect(getBackend) {
      this.getBackend = getBackend;
      this._connect();
    }

    async _connect() {
      clearTimeout(this.retryTimer);
      this.setStatus('offline', 'đang kết nối…');
      let b = null;
      try { b = await this.getBackend(); } catch (e) { b = null; }
      if (!b || !b.ok || !b.token) {
        this.setStatus('offline', 'chưa kết nối được backend');
        this.emit('offline');
        return this._retry();
      }
      this.backend = b;
      const ws = new WebSocket(b.url.replace(/^http/, 'ws') + '/ws?token=' + encodeURIComponent(b.token));
      this.ws = ws;
      ws.onopen = () => { this.retryMs = 1000; };
      ws.onmessage = ev => { try { this._handle(JSON.parse(ev.data)); } catch (e) { console.error(e); } };
      ws.onclose = () => {
        if (this.ws !== ws) return;
        this.ws = null;
        if (this.busy) this._handle({ type: 'error', message: 'Mất kết nối với backend.' });
        this.setStatus('offline', 'mất kết nối, thử lại…');
        this.emit('offline');
        this._retry();
      };
    }

    _retry() {
      this.retryMs = Math.min(this.retryMs * 1.5, 10000);
      this.retryTimer = setTimeout(() => this._connect(), this.retryMs);
    }

    async api(path, opts = {}) {
      if (!this.backend) throw new Error('offline');
      const r = await fetch(this.backend.url + path, {
        ...opts,
        headers: { 'content-type': 'application/json', 'x-cazung-token': this.backend.token, ...(opts.headers || {}) },
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }

    _send(obj) {
      if (this.ws && this.ws.readyState === 1) { this.ws.send(JSON.stringify(obj)); return true; }
      return false;
    }

    setStatus(kind, text) {
      $('status-dot').className = 'dot ' + kind;
      $('status-text').textContent = text;
    }

    setBusy(b) {
      this.busy = b;
      this.sendBtn.textContent = b ? '■' : '➤';
      this.sendBtn.title = b ? 'Dừng' : 'Gửi';
      this.sendBtn.classList.toggle('stop', b);
      if (this.backend && this.ws) this.setStatus(b ? 'busy' : 'online', b ? 'đang làm việc…' : this.model || 'online');
    }

    // ----------------------------------------------------------- events
    _handle(ev) {
      // processed strictly in order; chips/cards wait until the text before them has finished typing
      this.queue = this.queue.then(() => this._process(ev)).catch(e => console.error(e));
    }

    async _process(ev) {
      switch (ev.type) {
        case 'hello':
          this.model = ev.model;
          this.setStatus('online', ev.model);
          if (!this.loaded) this.loadHistory();
          this.emit('online', ev);
          break;
        case 'start':
          this.setBusy(true);
          this.cur = null;
          this.think = null;
          this.turnBubbles = [];
          this._showTyping();
          this.emit('start');
          break;
        case 'thinking':
          this._hideTyping();
          if (!this.think || this.think.closed) this.think = this._thinkBlock();
          this.think.tw.push(ev.text);
          this.emit('thinking');
          break;
        case 'emotion':
          this.emit('emotion', ev.value);
          break;
        case 'token':
          this._hideTyping();
          this._closeThink();
          this._ensureBubble().tw.push(ev.text);
          break;
        case 'retract':
          if (this.cur) this.cur.tw.set('');
          break;
        case 'tool_call':
          await this._settle();
          this._toolChip(ev);
          this._showTyping(`đang dùng ${ev.name}…`);
          this.emit('tool', ev);
          break;
        case 'tool_result':
          this._toolResult(ev);
          this._showTyping();
          this.emit('tool_result', ev);
          break;
        case 'confirm_request':
          await this._settle();
          this._confirmCard(ev);
          this.emit('confirm', ev);
          break;
        case 'done': {
          await this._settle();
          let bubbles = this.turnBubbles.filter(b => b.isConnected);
          if (!bubbles.length && ev.text) {
            this._ensureBubble().tw.push(ev.text);
            await this._settle();
            bubbles = this.turnBubbles.filter(b => b.isConnected);
          }
          const last = bubbles[bubbles.length - 1];
          if (last && ev.message_id) this._feedback(last, ev.message_id);
          this.setBusy(false);
          this.emit('done', ev);
          break;
        }
        case 'system':
          this._hideTyping();
          this._append('system', esc(ev.text));
          break;
        case 'error':
          await this._settle();
          this.setBusy(false);
          this._append('error', esc(ev.message));
          this.emit('error', ev.message);
          break;
        case 'reminder':
          this._append('reminder', '⏰ <b>Nhắc nhở:</b> ' + esc(ev.text));
          this.emit('reminder', ev);
          break;
      }
    }

    /** let the current bubble finish typing, then close it (and the thinking block) */
    async _settle() {
      this._hideTyping();
      this._closeThink();
      const cur = this.cur;
      if (!cur) return;
      this.cur = null;
      await cur.tw.idle();
      cur.el.classList.remove('streaming');
      if (!cur.tw.target.trim()) cur.el.remove();
      this.emit('typed-end');
    }

    _ensureBubble() {
      if (!this.cur) {
        const el = this._append('assistant streaming', '');
        const tw = new Typewriter(text => {
          el.innerHTML = md(text.replace(/^\s+/, ''));
          this._scroll();
          this.emit('typed', text);
        });
        this.cur = { el, tw };
        this.turnBubbles.push(el);
      }
      return this.cur;
    }

    _thinkBlock() {
      const d = document.createElement('details');
      d.className = 'think';
      d.open = true;
      d.innerHTML = '<summary>💭 <span class="lbl">Cazung đang suy nghĩ…</span> <span class="secs"></span></summary><div class="think-body"></div>';
      const body = d.querySelector('.think-body');
      const secs = d.querySelector('.secs');
      const started = performance.now();
      const timer = setInterval(() => { secs.textContent = Math.round((performance.now() - started) / 1000) + 's'; }, 500);
      this.list.appendChild(d);
      this._scroll(true);
      const tw = new Typewriter(text => {
        body.textContent = text.replace(/^\s+/, '');
        body.scrollTop = body.scrollHeight;
        this._scroll();
      }, 2);
      return { el: d, tw, timer, started, closed: false };
    }

    _closeThink() {
      const th = this.think;
      if (!th || th.closed) return;
      th.closed = true;
      clearInterval(th.timer);
      th.tw.finishNow();
      th.el.open = false;
      th.el.querySelector('.lbl').textContent = 'Đã suy nghĩ';
      th.el.querySelector('.secs').textContent = Math.max(1, Math.round((performance.now() - th.started) / 1000)) + 's';
    }

    _showTyping(label) {
      if (!this.typingEl) {
        this.typingEl = document.createElement('div');
        this.typingEl.className = 'msg assistant typing';
      }
      this.typingEl.innerHTML = '<span class="dots"><i></i><i></i><i></i></span>' +
        (label ? ` <span class="tlabel">${esc(label)}</span>` : '');
      this.list.appendChild(this.typingEl);
      this._scroll(true);
    }

    _hideTyping() { if (this.typingEl) this.typingEl.remove(); }

    _append(cls, html) {
      const el = document.createElement('div');
      el.className = 'msg ' + cls;
      el.innerHTML = html;
      if (this.typingEl && this.typingEl.isConnected) this.list.insertBefore(el, this.typingEl);
      else this.list.appendChild(el);
      this._scroll(true);
      return el;
    }

    _scroll(force) {
      const l = this.list;
      if (force || l.scrollHeight - l.scrollTop - l.clientHeight < 120) l.scrollTop = l.scrollHeight;
    }

    _toolChip(ev) {
      const args = Object.entries(ev.args || {}).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(', ');
      const d = document.createElement('details');
      d.className = 'tool';
      d.id = 'tool-' + ev.id;
      d.innerHTML = `<summary>${toolIcon(ev.name)} <b>${esc(ev.name)}</b> <span class="args">${esc(args.slice(0, 90))}${args.length > 90 ? '…' : ''}</span> <span class="st spin">◐</span></summary><pre></pre>`;
      this.list.appendChild(d);
      this._scroll(true);
    }

    _toolResult(ev) {
      const d = document.getElementById('tool-' + ev.id);
      if (!d) return;
      const st = d.querySelector('.st');
      st.textContent = ev.ok ? '✓' : '✗';
      st.className = 'st ' + (ev.ok ? 'ok' : 'bad');
      d.querySelector('pre').textContent = ev.preview || '';
    }

    _confirmCard(ev) {
      const el = document.createElement('div');
      el.className = 'confirm';
      const a = ev.args || {};
      let detail;
      if (ev.name === 'send_email') {
        detail = `Đến: ${a.to || ''}${a.cc ? '\nCC: ' + a.cc : ''}\nTiêu đề: ${a.subject || ''}\n\n${a.body || ''}`;
      } else if (ev.name === 'write_file') {
        detail = `${a.path}\n────────\n${String(a.content || '').slice(0, 1500)}`;
      } else if (ev.name === 'edit_file') {
        detail = `${a.path}\n− ${String(a.old || '').slice(0, 600)}\n+ ${String(a.new || '').slice(0, 600)}`;
      } else {
        detail = a.command || a.code || a.target || a.path || JSON.stringify(a, null, 2);
      }
      el.innerHTML = `⚠️ Cazung xin phép dùng <b>${esc(ev.name)}</b>:<pre>${esc(detail)}</pre>
        <div class="row"><button class="no">Từ chối</button><button class="yes">Cho phép</button></div>`;
      const answer = ok => {
        this._send({ type: 'confirm', id: ev.id, approved: ok });
        el.classList.add('done');
        el.querySelector('.row').innerHTML = ok ? '✓ Đã cho phép' : '✗ Đã từ chối';
        this._showTyping(ok ? `đang chạy ${ev.name}…` : '');
        this.emit('confirmed', ok);
      };
      el.querySelector('.yes').onclick = () => answer(true);
      el.querySelector('.no').onclick = () => answer(false);
      this.list.appendChild(el);
      this._scroll(true);
    }

    _feedback(el, mid) {
      el.classList.add('has-fb');
      const fb = document.createElement('div');
      fb.className = 'fb';
      fb.innerHTML = '<button class="up" title="Hay">👍</button><button class="down" title="Chưa ổn – Cazung sẽ rút kinh nghiệm">👎</button>';
      fb.querySelector('.up').onclick = () => {
        this._send({ type: 'feedback', message_id: mid, rating: 1 });
        fb.classList.add('voted'); fb.innerHTML = '<button class="on">👍</button>';
        this.emit('feedback', 1);
      };
      fb.querySelector('.down').onclick = () => {
        fb.classList.add('voted');
        fb.innerHTML = '<input placeholder="Sai chỗ nào? (Enter)" style="font-size:11px;width:170px;border:2px solid #2a1b33">';
        const inp = fb.querySelector('input');
        inp.focus();
        inp.onkeydown = e => {
          if (e.key !== 'Enter') return;
          this._send({ type: 'feedback', message_id: mid, rating: -1, comment: inp.value });
          fb.innerHTML = '<button class="on">👎</button>';
          this.emit('feedback', -1);
        };
      };
      el.appendChild(fb);
    }

    async loadHistory() {
      try {
        const rows = await this.api('/api/history?limit=60');
        this.loaded = true;
        this.list.innerHTML = '';
        rows.forEach(r => {
          if (r.role === 'user') this._append('user', md(r.text));
          else this._feedback(this._append('assistant', md(r.text)), r.id);
        });
        if (!rows.length) this._append('system', 'Chưa có tin nhắn nào. Gõ <b>/help</b> để xem lệnh nhanh.');
      } catch (e) { /* will retry on next hello */ }
    }

    // ------------------------------------------------------------ input
    send(text) {
      text = text.trim();
      if (!text) return;
      if (!this._send({ type: 'chat', text })) {
        this._append('error', 'Chưa kết nối được với Cazung. Kiểm tra backend/Ollama nha.');
        return;
      }
      this._append('user', md(text));
      this._showTyping(); // instant feedback before the first token arrives
      this.emit('sent', text);
    }

    newSession() {
      if (this._send({ type: 'chat', text: '/new' })) this.list.innerHTML = '';
    }

    _bindUI() {
      $('composer').addEventListener('submit', e => {
        e.preventDefault();
        if (this.busy) { this._send({ type: 'stop' }); return; }
        const t = this.input.value;
        this.input.value = '';
        this._grow();
        this.send(t);
      });
      this.input.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
          e.preventDefault();
          if (!this.busy) $('composer').requestSubmit();
        }
      });
      this.input.addEventListener('input', () => this._grow());
      $('btn-close').onclick = () => this.close();
      $('btn-new').onclick = () => this.newSession();
      $('btn-memory').onclick = () => this.toggleMemory();
      $('mem-tabs').addEventListener('click', e => {
        const b = e.target.closest('button');
        if (!b) return;
        this.tab = b.dataset.tab;
        [...$('mem-tabs').children].forEach(x => x.classList.toggle('active', x === b));
        this.renderMemory();
      });
      $('mem-add').addEventListener('submit', async e => {
        e.preventDefault();
        const v = $('mem-input').value.trim();
        if (!v) return;
        const kind = { lesson: 'lesson', events: 'note', skill: 'skill' }[this.tab] || 'fact';
        await this.api('/api/memories', { method: 'POST', body: JSON.stringify({ content: v, kind, importance: 0.85 }) });
        $('mem-input').value = '';
        this.renderMemory();
      });
    }

    _grow() {
      this.input.style.height = 'auto';
      this.input.style.height = Math.min(this.input.scrollHeight + 4, 110) + 'px';
      this.input.style.overflowY = this.input.scrollHeight > 106 ? 'auto' : 'hidden';
    }

    // ----------------------------------------------------------- memory
    toggleMemory() {
      const show = this.memView.classList.contains('hidden');
      this.memView.classList.toggle('hidden', !show);
      this.list.classList.toggle('hidden', show);
      $('composer').classList.toggle('hidden', show);
      if (show) this.renderMemory();
    }

    async renderMemory() {
      const box = $('mem-list');
      $('mem-add').classList.toggle('hidden', this.tab === 'stats');
      box.innerHTML = '<div class="empty">Đang tải…</div>';
      try {
        if (this.tab === 'stats') return this._renderStats(box);
        const rows = await this.api('/api/memories?kind=' + TABS[this.tab]);
        if (!rows.length) { box.innerHTML = '<div class="empty">Chưa có gì ở đây. Cazung sẽ tự học dần khi trò chuyện ♥</div>'; return; }
        box.innerHTML = '';
        rows.forEach(r => {
          const el = document.createElement('div');
          el.className = 'mem';
          el.innerHTML = `<span class="kind">${KIND_LABEL[r.kind] || r.kind}</span><span class="text">${esc(r.content)}</span>
            <span class="imp" title="Độ quan trọng ${r.importance.toFixed(2)}"><i style="width:${Math.round(r.importance * 100)}%"></i></span>
            <button class="del" title="Quên điều này">✕</button>`;
          el.querySelector('.del').onclick = async () => {
            await this.api('/api/memories/' + r.id, { method: 'DELETE' });
            el.remove();
          };
          box.appendChild(el);
        });
      } catch (e) {
        box.innerHTML = '<div class="empty">Không tải được (backend offline?)</div>';
      }
    }

    async _renderStats(box) {
      const s = await this.api('/api/stats');
      const c = s.context || {};
      const pct = c.history_budget ? Math.min(100, Math.round((c.window_tokens || 0) / c.history_budget * 100)) : 0;
      const mem = Object.entries(s.memories || {}).map(([k, v]) => `${KIND_LABEL[k] || k}: <b>${v}</b>`).join(' · ') || 'chưa có';
      box.innerHTML = `<div class="stats">
        Tổng tin nhắn đã lưu: <b>${s.messages_total}</b> · lượt trò chuyện: <b>${s.turns}</b><br>
        Ký ức: ${mem}<br>
        Embedding: <b>${esc(s.embedding)}</b> · việc nền: <b>${s.background_jobs}</b>${s.background_current ? ' (đang: ' + esc(s.background_current) + ')' : ''}<br>
        Ngữ cảnh lượt trước: <b>${c.window_messages ?? '-'}</b> tin gần nhất (${c.window_tokens ?? 0}/${c.history_budget ?? 0} token),
        nhớ lại <b>${c.recalled ?? 0}</b> ký ức, <b>${c.lessons ?? 0}</b> bài học
        <div class="bar"><i style="width:${pct}%"></i></div>
        Tin chưa nén: <b>${s.unsummarized}</b><br>
        <b>Tóm tắt cuộc trò chuyện hiện tại:</b>
        <pre>${esc(s.summary || '(chưa cần tóm tắt – cuộc trò chuyện còn ngắn)')}</pre>
      </div>`;
    }
  }

  window.Chat = Chat;
})();
