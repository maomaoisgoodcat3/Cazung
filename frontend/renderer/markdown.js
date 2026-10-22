/* Tiny, safe Markdown -> HTML (code blocks, inline code, bold/italic, links, lists, headings). */
(function (root) {
  'use strict';
  const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  function inline(s) {
    const codes = [];
    s = s.replace(/`([^`\n]+)`/g, (_, c) => { codes.push(c); return '\u0000' + (codes.length - 1) + '\u0000'; });
    s = esc(s)
      .replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>')
      .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<i>$2</i>')
      .replace(/\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noreferrer">$2</a>');
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => '<code>' + esc(codes[+i]) + '</code>');
  }

  function render(md) {
    const out = [];
    const parts = String(md || '').split(/```/);
    parts.forEach((part, idx) => {
      if (idx % 2 === 1) { // code block
        const nl = part.indexOf('\n');
        const body = nl >= 0 && /^[\w+#.-]*$/.test(part.slice(0, nl).trim()) ? part.slice(nl + 1) : part;
        out.push('<pre><code>' + esc(body.replace(/\n$/, '')) + '</code></pre>');
        return;
      }
      let list = null, para = [];
      const flushPara = () => { if (para.length) { out.push('<p>' + para.map(inline).join('<br>') + '</p>'); para = []; } };
      const flushList = () => {
        if (!list) return;
        const start = list.start > 1 ? ` start="${list.start}"` : '';
        out.push(`<${list.tag}${start}>` + list.items.map(i => '<li>' + inline(i) + '</li>').join('') + `</${list.tag}>`);
        list = null;
      };
      part.split('\n').forEach(line => {
        let m;
        if ((m = line.match(/^\s*[-*•]\s+(.*)/))) {
          flushPara(); if (!list || list.tag !== 'ul') { flushList(); list = { tag: 'ul', items: [] }; } list.items.push(m[1]);
        } else if ((m = line.match(/^\s*(\d+)[.)]\s+(.*)/))) {
          flushPara();
          if (!list || list.tag !== 'ol') { flushList(); list = { tag: 'ol', items: [], start: Number(m[1]) }; }
          list.items.push(m[2]);
        } else if ((m = line.match(/^#{1,6}\s+(.*)/))) {
          flushPara(); flushList(); out.push('<h4>' + inline(m[1]) + '</h4>');
        } else if (!line.trim()) {
          flushPara(); flushList();
        } else {
          flushList(); para.push(line);
        }
      });
      flushPara(); flushList();
    });
    return out.join('');
  }

  root.Markdown = { render, esc };
})(typeof self !== 'undefined' ? self : this);
