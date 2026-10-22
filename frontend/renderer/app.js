/* Glue: connects the pet, the chat panel and the desktop shell (Electron). */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const desktop = window.cazungDesktop || null;
  if (!desktop) document.body.classList.add('browser');

  const canvas = $('pet');
  const bubble = $('bubble');
  const pet = new window.Pet(canvas, { scale: 4, desktop });
  const chat = new window.Chat();
  window.__chat = chat; // handy for debugging from devtools
  let cfg = { name: 'Cazung', user_title: 'chủ nhân', pet: { scale: 4, wander: true, idle_chatter: true, sleep_after_minutes: 5 } };
  let greeted = false;

  const pick = arr => arr[Math.floor(Math.random() * arr.length)];
  const U = () => cfg.user_title;

  // --------------------------------------------------------------- layout
  function layout() {
    const h = canvas.height;
    chat.panel.style.bottom = (h + 2) + 'px'; // leave room for thought clouds / effects above her head
    bubble.style.bottom = (h - 14) + 'px';
    if (desktop) desktop.setPetRect({ left: canvas.offsetLeft, right: canvas.offsetLeft + canvas.width });
  }
  layout();

  // --------------------------------------------------------------- bubble
  let bubbleTimer = null;
  function say(text, ms = 6000, html = false) {
    if (chat.isOpen) return;
    clearTimeout(bubbleTimer);
    if (html) bubble.innerHTML = text; else bubble.textContent = text;
    bubble.classList.remove('hidden');
    if (ms) bubbleTimer = setTimeout(hideBubble, ms);
  }
  function hideBubble() { bubble.classList.add('hidden'); }
  bubble.addEventListener('click', () => { hideBubble(); openChat(); });

  function openChat() {
    hideBubble();
    pet.touch();
    if (pet.mode === 'walk') pet.setMode('idle');
    chat.open();
    if (desktop) { desktop.ensureVisible(); desktop.focus(); }
  }

  // ------------------------------------------------- click-through (desktop)
  if (desktop) {
    let ignoring = true;
    const setIgnore = v => { if (v !== ignoring) { ignoring = v; desktop.setIgnoreMouse(v); } };
    document.addEventListener('mousemove', e => {
      if (pet.dragging || pet.down) return setIgnore(false);
      const el = document.elementFromPoint(e.clientX, e.clientY);
      let over = !!(el && el.closest('.interactive'));
      if (over && el === canvas) over = pet.hitTest(e.clientX, e.clientY);
      setIgnore(!over);
    });
    document.addEventListener('mouseleave', () => { if (!pet.dragging) setIgnore(true); });
    desktop.on('landed', () => {
      pet.landed();
      if (Math.random() < 0.4) say(pick(['Úi da!', 'Hạ cánh an toàn~ ✨', 'Đừng thả em mạnh vậy chứ >.<', 'Chóng mặt quá…']), 2500);
    });
    desktop.on('toggle-chat', () => (chat.isOpen ? chat.close() : openChat()));
    desktop.on('menu', action => {
      if (action === 'new') { openChat(); chat.newSession(); }
      else if (action === 'sleep') { chat.close(); pet.setMode('sleep'); }
      else if (action === 'wake') { pet.touch(); pet.setMode('wave'); }
      else if (action === 'wander-on') pet.wander = true;
      else if (action === 'wander-off') pet.wander = false;
      else if (action === 'memory') { openChat(); chat.toggleMemory(); }
    });
  }

  // ------------------------------------------------------------ pet input
  pet.canIdleActs = () => !chat.isOpen && !chat.busy;
  pet.on('click', () => (chat.isOpen ? chat.close() : openChat()));
  pet.on('contextmenu', () => { if (desktop) desktop.showMenu({ wander: pet.wander, sleeping: pet.mode === 'sleep' }); });
  pet.on('dragstart', () => hideBubble());
  pet.on('dblclick', () => say(pick(['Hihi~ ♪', 'Lalala~ ♫', 'Nhìn em nhảy nè!', 'Yay! ✨']), 2000));
  // eyes follow the cursor when it is close
  document.addEventListener('mousemove', e => {
    const r = canvas.getBoundingClientRect();
    const dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
    if (Math.hypot(dx, dy) < 260) pet.lookAt(dx);
  });
  pet.on('petted', () => {
    pet.setEmotion('love', 2500);
    say(pick([`Hihi, nhột quá ${U()} ơi~`, `${U()} xoa đầu em kìa ♥`, 'Ehehe~ thêm chút nữa đi~', 'Em ngoan mà, đúng không? ✨']), 3000);
  });

  // ---------------------------------------------------- chat -> reactions
  chat.on('online', () => {
    applyConfig();
    pet.setEmotion('happy', 2500);
  });
  chat.on('offline', () => { pet.setEmotion('sleepy', 4000); });
  const TOOL_ANIM = [
    [/__/, 'work'], [/email/, 'mail'], [/search_code|file|tree|outline|python|shell/, 'work'],
    [/search|fetch|weather|open_url/, 'search'], [/process|system|network|startup|installed|scan/, 'scan'],
  ];
  chat.on('sent', () => { pet.touch(); pet.act('think'); });
  chat.on('start', () => pet.act('think'));
  chat.on('thinking', () => pet.act('think'));
  chat.on('tool', ev => pet.act((TOOL_ANIM.find(([rx]) => rx.test(ev.name)) || [0, 'think'])[1]));
  chat.on('tool_result', () => pet.act('think'));
  chat.on('confirmed', ok => { if (!ok) pet.setEmotion('neutral', 1500); });
  chat.on('emotion', e => pet.setEmotion(e, 15000));
  chat.on('typed', text => {
    pet.act('talk');
    if (!chat.isOpen) say(text.length > 150 ? '…' + text.slice(-150) : text, 0);
  });
  chat.on('typed-end', () => pet.act(chat.busy ? 'think' : 'idle'));
  chat.on('done', ev => {
    pet.act('idle');
    if (ev.emotion) { pet.setEmotion(ev.emotion, 7000); pet.react(ev.emotion); }
    if (!chat.isOpen && ev.text) {
      const t = ev.text.length > 170 ? window.Markdown.esc(ev.text.slice(0, 170)) + '… <span class="more">(bấm để xem)</span>'
        : window.Markdown.esc(ev.text);
      say(t, 14000, true);
    }
  });
  chat.on('error', msg => {
    pet.act('idle');
    pet.setEmotion('sad', 6000);
    say('Huhu, có lỗi rồi: ' + msg, 10000);
  });
  chat.on('confirm', () => { openChat(); pet.setEmotion('thinking', 8000); });
  chat.on('feedback', r => pet.setEmotion(r > 0 ? 'love' : 'sad', 3000));
  chat.on('reminder', ev => {
    pet.touch();
    pet.setMode('alert');
    setTimeout(() => { if (pet.mode === 'alert') pet.setMode('idle'); }, 4000);
    say('⏰ ' + ev.text, 60000);
    try { new Notification('Cazung nhắc nè ⏰', { body: ev.text, silent: false }); } catch (e) { /* no notifications */ }
  });
  chat.on('open', () => hideBubble());

  document.addEventListener('keydown', e => { if (e.key === 'Escape' && chat.isOpen) chat.close(); });

  // ----------------------------------------------------------- config
  async function applyConfig() {
    try {
      cfg = await chat.api('/api/config');
      const p = cfg.pet || {};
      pet.setScale(p.scale || 4);
      pet.wander = p.wander !== false;
      pet.sleepAfterMs = (p.sleep_after_minutes || 5) * 60000;
      layout();
    } catch (e) { /* keep defaults */ }
    if (!greeted) {
      greeted = true;
      pet.setMode('wave');
      say(greeting(), 5000);
    }
  }

  function greeting() {
    const h = new Date().getHours();
    if (h < 5) return `Khuya rồi đó ${U()}, đừng thức khuya quá nha~`;
    if (h < 11) return `Chào buổi sáng ${U()}! Hôm nay mình làm gì nè? ☀️`;
    if (h < 14) return `${U()} ăn trưa chưa? Cazung có mặt rồi đây~`;
    if (h < 18) return `Chiều rồi nè ${U()}, cần em giúp gì không?`;
    return `Tối vui vẻ nha ${U()}! Cazung đây~ 🌙`;
  }

  // ------------------------------------------------------- idle chatter
  setInterval(() => {
    if (!cfg.pet || cfg.pet.idle_chatter === false) return;
    if (chat.isOpen || chat.busy || pet.mode !== 'idle' || Math.random() > 0.12) return;
    const h = new Date().getHours();
    const lines = [
      `${U()} cần gì cứ bấm vào em nha~`,
      'Hmm~ hôm nay trời đẹp ghê…',
      `${U()} nhớ uống nước đó!`,
      'Em đang học thêm nhiều thứ mới nè ✨',
      'Chán quá… chơi với em đi~',
      `${U()} làm việc chăm chỉ quá, nghỉ chút đi nè`,
    ];
    if (h >= 23 || h < 5) lines.push(`Khuya rồi, ${U()} đi ngủ sớm nha…`);
    if (h >= 11 && h <= 13) lines.push('Tới giờ ăn trưa rồi kìa!');
    pet.setEmotion(pick(['happy', 'smug', 'neutral', 'shy']), 4000);
    say(pick(lines), 5000);
  }, 60000);

  // ------------------------------------------------------------ connect
  const getBackend = desktop
    ? () => desktop.getBackend()
    : () => Promise.resolve({
        ok: true,
        url: location.origin,
        token: (document.querySelector('meta[name="cazung-token"]') || {}).content || '',
      });
  chat.connect(getBackend);
  if (!desktop) chat.open();
})();
