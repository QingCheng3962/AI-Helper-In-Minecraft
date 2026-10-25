/* QQ 图片前端：只读、轮询、全部使用 textContent（不拼 HTML）。 */
(() => {
  'use strict';

  const app = document.getElementById('qq-app');
  const list = document.getElementById('qq-list');
  const statusEl = document.getElementById('qq-status');
  const emptyEl = document.getElementById('qq-empty');
  if (!app || !list) return;

  const imageBase = app.dataset.imageBase || '';
  let allowedImageHost = location.host;
  try {
    if (imageBase) allowedImageHost = new URL(imageBase).host;
  } catch (_) { /* 配置错误时退回同源 */ }

  let lastId = 0;
  let busy = false;

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
  }

  function safeImageUrl(raw) {
    if (!raw) return '';
    try {
      const url = new URL(raw, location.href);
      if (url.protocol !== 'http:' && url.protocol !== 'https:') return '';
      if (url.origin === location.origin) return url.href;
      if (allowedImageHost && url.host === allowedImageHost) return url.href;
      return '';
    } catch (_) {
      return '';
    }
  }

  function formatTime(seconds) {
    const date = new Date((Number(seconds) || 0) * 1000);
    return isNaN(date.getTime()) ? '' : date.toLocaleString();
  }

  function renderMessage(msg) {
    const article = document.createElement('article');
    article.className = 'message';

    const head = document.createElement('div');
    head.className = 'message-head';

    const who = document.createElement('span');
    who.className = 'who';
    who.textContent = `[${msg.group_name || '群'}] ${msg.sender || '未知'}`;

    const time = document.createElement('time');
    time.className = 'time';
    time.textContent = formatTime(msg.time);

    head.appendChild(who);
    head.appendChild(time);
    article.appendChild(head);

    if (msg.text) {
      const text = document.createElement('p');
      text.className = 'text';
      text.textContent = msg.text;      // 关键：绝不使用 innerHTML
      article.appendChild(text);
    }

    for (const img of msg.images || []) {
      const src = safeImageUrl(img.url);
      if (!src) continue;
      const link = document.createElement('a');
      link.href = src;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      const el = document.createElement('img');
      el.src = src;
      el.alt = img.name || 'QQ 图片';
      el.loading = 'lazy';
      el.referrerPolicy = 'no-referrer';
      link.appendChild(el);
      article.appendChild(link);
    }

    return article;
  }

  async function poll() {
    if (busy) return;
    busy = true;
    try {
      const resp = await fetch(`api.php?since=${lastId}&limit=50`, {
        credentials: 'same-origin',
        headers: { 'Accept': 'application/json' },
        cache: 'no-store',
      });
      if (resp.status === 401 || resp.status === 403) {
        location.replace('login.php');
        return;
      }
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (!data || data.ok !== true || !Array.isArray(data.messages)) {
        throw new Error('返回格式错误');
      }

      for (const msg of data.messages) {
        const id = Number(msg.id) || 0;
        if (id <= lastId) continue;
        list.appendChild(renderMessage(msg));
        lastId = id;
      }

      if (lastId > 0 && emptyEl) emptyEl.hidden = true;
      setStatus(`已连接 · ${new Date().toLocaleTimeString()}`);
    } catch (err) {
      setStatus('连接失败，3 秒后重试');
    } finally {
      busy = false;
    }
  }

  setInterval(poll, 3000);
  poll();
})();
