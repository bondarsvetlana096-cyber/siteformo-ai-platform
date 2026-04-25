(function () {
  const script = document.currentScript;
  const apiBase = (script && script.dataset.apiBase ? script.dataset.apiBase : '').replace(/\/$/, '');
  const root = document.createElement('div');
  root.id = 'siteformo-guided-chat';
  document.body.appendChild(root);

  const style = document.createElement('style');
  style.textContent = `
    #siteformo-guided-chat{position:fixed;right:18px;bottom:18px;z-index:99999;font-family:Inter,Arial,sans-serif;color:#111827}
    #siteformo-guided-chat *{box-sizing:border-box}
    .sf-toggle{border:0;border-radius:999px;background:#111827;color:white;padding:14px 18px;font-weight:800;box-shadow:0 12px 35px rgba(0,0,0,.22);cursor:pointer}
    .sf-panel{display:none;width:min(420px,calc(100vw - 28px));background:white;border:1px solid #e5e7eb;border-radius:22px;box-shadow:0 18px 60px rgba(0,0,0,.22);overflow:hidden}
    .sf-panel.sf-open{display:block}
    .sf-head{padding:16px 18px;background:#111827;color:#fff;font-weight:800;display:flex;justify-content:space-between;gap:12px;align-items:center}
    .sf-close{background:transparent;border:0;color:white;font-size:20px;cursor:pointer}
    .sf-body{padding:18px;display:flex;flex-direction:column;gap:12px;max-height:70vh;overflow:auto}
    .sf-message{font-size:16px;line-height:1.45;font-weight:650;white-space:pre-wrap}
    .sf-estimate,.sf-confirm{border:1px solid #e5e7eb;background:#f9fafb;border-radius:14px;padding:10px 12px;font-size:14px;line-height:1.4;white-space:pre-wrap}
    .sf-options,.sf-fields{display:flex;flex-direction:column;gap:9px}
    .sf-option{border:1px solid #d1d5db;background:#fff;border-radius:14px;padding:11px 12px;text-align:left;cursor:pointer;font-weight:650}
    .sf-option:hover{background:#f9fafb}
    .sf-contact{display:flex;gap:8px}
    .sf-field label{display:block;font-size:13px;font-weight:750;margin-bottom:5px}
    .sf-input,.sf-textarea{width:100%;border:1px solid #d1d5db;border-radius:12px;padding:11px;font-size:14px;min-width:0}
    .sf-textarea{min-height:78px;resize:vertical}
    .sf-send{border:0;border-radius:12px;background:#111827;color:white;padding:11px 14px;font-weight:800;cursor:pointer}
    .sf-cta,.sf-offer{display:inline-flex;justify-content:center;text-decoration:none;border-radius:14px;background:#111827;color:white;padding:12px 14px;font-weight:800;text-align:center}
    .sf-offer{background:#fff;color:#111827;border:1px solid #111827}
    .sf-error{color:#b91c1c;font-size:13px}
    @media(max-width:480px){#siteformo-guided-chat{left:14px;right:14px}.sf-panel{width:100%}.sf-toggle{width:100%}}
  `;
  document.head.appendChild(style);

  let sessionId = localStorage.getItem('siteformo_guided_session_id') || '';
  let current = null;

  root.innerHTML = `
    <button class="sf-toggle" type="button">Create full page</button>
    <section class="sf-panel" aria-live="polite">
      <div class="sf-head"><span>SiteFormo AI</span><button class="sf-close" type="button" aria-label="Close">×</button></div>
      <div class="sf-body"><div class="sf-message">Loading...</div><div class="sf-estimate" style="display:none"></div><div class="sf-confirm" style="display:none"></div><div class="sf-options"></div><div class="sf-error"></div></div>
    </section>
  `;

  const toggle = root.querySelector('.sf-toggle');
  const panel = root.querySelector('.sf-panel');
  const close = root.querySelector('.sf-close');
  const message = root.querySelector('.sf-message');
  const estimateBox = root.querySelector('.sf-estimate');
  const confirmBox = root.querySelector('.sf-confirm');
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
    if (!res.ok) throw new Error(data.detail || 'Connection error');
    if (data.session_id) {
      sessionId = data.session_id;
      localStorage.setItem('siteformo_guided_session_id', sessionId);
    }
    return data;
  }

  function addLink(className, label, url) {
    if (!url) return;
    const a = document.createElement('a');
    a.className = className;
    a.href = url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.textContent = label;
    options.appendChild(a);
  }

  function renderFields(data) {
    const wrap = document.createElement('div');
    wrap.className = 'sf-fields';
    (data.fields || []).forEach((field) => {
      const block = document.createElement('div');
      block.className = 'sf-field';
      const id = 'sf-field-' + field.name;
      const inputTag = field.type === 'textarea' ? 'textarea' : 'input';
      block.innerHTML = `<label for="${id}">${field.label}${field.required ? ' *' : ''}</label><${inputTag} id="${id}" class="${field.type === 'textarea' ? 'sf-textarea' : 'sf-input'}" data-name="${field.name}" placeholder="${field.placeholder || ''}"></${inputTag}>`;
      wrap.appendChild(block);
    });
    const send = document.createElement('button');
    send.className = 'sf-send';
    send.type = 'button';
    send.textContent = 'Continue';
    send.addEventListener('click', () => {
      const answer = {};
      wrap.querySelectorAll('[data-name]').forEach((el) => { answer[el.dataset.name] = el.value; });
      submit(answer);
    });
    wrap.appendChild(send);
    options.appendChild(wrap);
  }

  function render(data) {
    current = data;
    setError('');
    message.textContent = data.message || '';
    options.innerHTML = '';
    estimateBox.style.display = 'none';
    confirmBox.style.display = 'none';

    if (data.estimate) {
      estimateBox.style.display = '';
      estimateBox.textContent = `Estimate: from €${data.estimate.price_eur}. 50% deposit: €${data.deposit_due_eur || Math.round(data.estimate.price_eur / 2)}. Timeline: ${data.estimate.timeline}`;
    }

    if (data.confirmation) {
      confirmBox.style.display = '';
      confirmBox.textContent = `Generated message:\n${data.confirmation.message}`;
      addLink('sf-cta', data.confirmation.label || 'Send confirmation', data.confirmation.url);
    }

    if (data.input_type === 'fields') {
      renderFields(data);
      return;
    }

    if (data.input_type === 'text') {
      const wrap = document.createElement('div');
      wrap.className = 'sf-contact';
      wrap.innerHTML = '<input class="sf-input" placeholder="email, WhatsApp, Telegram" /><button class="sf-send" type="button">OK</button>';
      const input = wrap.querySelector('.sf-input');
      const send = wrap.querySelector('.sf-send');
      send.addEventListener('click', () => submit(input.value));
      input.addEventListener('keydown', (event) => { if (event.key === 'Enter') submit(input.value); });
      options.appendChild(wrap);
      input.focus();
      return;
    }

    if (data.is_complete) {
      if (data.offer) addLink('sf-offer', data.offer.label || 'Open offer', data.offer.url);
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
