(function () {
  const script = document.currentScript;
  const apiBase = (script && script.dataset.apiBase ? script.dataset.apiBase : '').replace(/\/$/, '');
  const ctaUrl = script && script.dataset.ctaUrl ? script.dataset.ctaUrl : 'https://siteformo.com';
  const root = document.createElement('div');
  root.id = 'siteformo-guided-chat';
  document.body.appendChild(root);

  const style = document.createElement('style');
  style.textContent = `
    #siteformo-guided-chat{position:fixed;right:18px;bottom:18px;z-index:99999;font-family:Inter,Arial,sans-serif;color:#111827}
    #siteformo-guided-chat *{box-sizing:border-box}
    .sf-toggle{border:0;border-radius:999px;background:#111827;color:white;padding:14px 18px;font-weight:700;box-shadow:0 12px 35px rgba(0,0,0,.22);cursor:pointer}
    .sf-panel{display:none;width:min(360px,calc(100vw - 28px));background:white;border:1px solid #e5e7eb;border-radius:22px;box-shadow:0 18px 60px rgba(0,0,0,.22);overflow:hidden}
    .sf-panel.sf-open{display:block}
    .sf-head{padding:16px 18px;background:#111827;color:#fff;font-weight:800;display:flex;justify-content:space-between;gap:12px;align-items:center}
    .sf-close{background:transparent;border:0;color:white;font-size:20px;cursor:pointer}
    .sf-body{padding:18px;display:flex;flex-direction:column;gap:12px}
    .sf-message{font-size:16px;line-height:1.45;font-weight:650}
    .sf-options{display:flex;flex-direction:column;gap:8px}
    .sf-option{border:1px solid #d1d5db;background:#fff;border-radius:14px;padding:11px 12px;text-align:left;cursor:pointer;font-weight:650}
    .sf-option:hover{background:#f9fafb}
    .sf-contact{display:flex;gap:8px}
    .sf-input{flex:1;border:1px solid #d1d5db;border-radius:12px;padding:11px;font-size:14px;min-width:0}
    .sf-send{border:0;border-radius:12px;background:#111827;color:white;padding:0 14px;font-weight:750;cursor:pointer}
    .sf-cta{display:inline-flex;justify-content:center;text-decoration:none;border-radius:14px;background:#111827;color:white;padding:12px 14px;font-weight:800}
    .sf-error{color:#b91c1c;font-size:13px}
    @media(max-width:480px){#siteformo-guided-chat{left:14px;right:14px}.sf-panel{width:100%}.sf-toggle{width:100%}}
  `;
  document.head.appendChild(style);

  let sessionId = localStorage.getItem('siteformo_guided_session_id') || '';
  let current = null;

  root.innerHTML = `
    <button class="sf-toggle" type="button">AI-квиз SiteFormo</button>
    <section class="sf-panel" aria-live="polite">
      <div class="sf-head"><span>SiteFormo AI</span><button class="sf-close" type="button" aria-label="Закрыть">×</button></div>
      <div class="sf-body"><div class="sf-message">Загрузка...</div><div class="sf-options"></div><div class="sf-error"></div></div>
    </section>
  `;

  const toggle = root.querySelector('.sf-toggle');
  const panel = root.querySelector('.sf-panel');
  const close = root.querySelector('.sf-close');
  const message = root.querySelector('.sf-message');
  const options = root.querySelector('.sf-options');
  const error = root.querySelector('.sf-error');

  function setError(text) { error.textContent = text || ''; }

  async function post(path, payload) {
    const res = await fetch(apiBase + path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload || {})
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Ошибка связи с сервером');
    if (data.session_id) {
      sessionId = data.session_id;
      localStorage.setItem('siteformo_guided_session_id', sessionId);
    }
    return data;
  }

  function render(data) {
    current = data;
    setError('');
    message.textContent = data.message || '';
    options.innerHTML = '';

    if (data.input_type === 'text') {
      const wrap = document.createElement('div');
      wrap.className = 'sf-contact';
      wrap.innerHTML = '<input class="sf-input" placeholder="@telegram, +353..., email" /><button class="sf-send" type="button">OK</button>';
      const input = wrap.querySelector('.sf-input');
      const send = wrap.querySelector('.sf-send');
      send.addEventListener('click', () => submit(input.value));
      input.addEventListener('keydown', (event) => { if (event.key === 'Enter') submit(input.value); });
      options.appendChild(wrap);
      input.focus();
      return;
    }

    if (data.is_complete && data.cta) {
      const a = document.createElement('a');
      a.className = 'sf-cta';
      a.href = data.cta.url || ctaUrl;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.textContent = data.cta.label || 'Перейти на SiteFormo';
      options.appendChild(a);
    }

    (data.options || []).forEach((opt) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'sf-option';
      btn.textContent = opt.label;
      btn.addEventListener('click', () => submit(opt.value));
      options.appendChild(btn);
    });
  }

  async function start(reset) {
    try { render(await post('/channels/web-chat/start', { session_id: sessionId, reset: !!reset })); }
    catch (e) { setError(e.message); }
  }

  async function submit(answer) {
    try { render(await post('/channels/web-chat', { session_id: sessionId, answer: answer })); }
    catch (e) { setError(e.message); }
  }

  toggle.addEventListener('click', () => {
    panel.classList.add('sf-open');
    toggle.style.display = 'none';
    if (!current) start(false);
  });
  close.addEventListener('click', () => {
    panel.classList.remove('sf-open');
    toggle.style.display = '';
  });
})();
