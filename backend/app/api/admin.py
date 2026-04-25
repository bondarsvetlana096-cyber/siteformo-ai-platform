from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["admin"])


@router.get("/admin", response_class=HTMLResponse)
def admin_page():
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Siteformo Leads Admin</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; background: #f7f7f8; color: #111; }
    h1 { margin-bottom: 8px; }
    .card { background: white; border-radius: 14px; padding: 16px; box-shadow: 0 2px 12px rgba(0,0,0,.06); margin-bottom: 16px; }
    input, select, button { padding: 10px; border-radius: 10px; border: 1px solid #ddd; margin: 4px; }
    button { cursor: pointer; background: #111; color: white; }
    table { width: 100%; border-collapse: collapse; background: white; border-radius: 14px; overflow: hidden; }
    th, td { text-align: left; padding: 12px; border-bottom: 1px solid #eee; vertical-align: top; }
    th { background: #fafafa; }
    .muted { color: #666; font-size: 13px; }
    .error { color: #b00020; }
  </style>
</head>
<body>
  <h1>Siteformo Leads Admin</h1>
  <p class="muted">Enter ADMIN_API_KEY from Railway Variables.</p>

  <div class="card">
    <input id="key" placeholder="ADMIN_API_KEY" style="width: 260px;" />
    <input id="city" placeholder="City" />
    <input id="service" placeholder="Service" />
    <select id="status">
      <option value="">All statuses</option>
      <option value="new">new</option>
      <option value="contacted">contacted</option>
      <option value="qualified">qualified</option>
      <option value="closed">closed</option>
      <option value="lost">lost</option>
    </select>
    <button onclick="loadLeads()">Load leads</button>
  </div>

  <div id="msg"></div>
  <table>
    <thead>
      <tr>
        <th>ID</th><th>Created</th><th>Channel</th><th>Service</th><th>City</th><th>Urgency</th><th>Contact</th><th>Status</th><th>Text</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>

<script>
async function loadLeads() {
  const key = document.getElementById('key').value;
  const city = document.getElementById('city').value;
  const service = document.getElementById('service').value;
  const status = document.getElementById('status').value;

  const params = new URLSearchParams();
  if (city) params.set('city', city);
  if (service) params.set('service', service);
  if (status) params.set('status', status);

  const msg = document.getElementById('msg');
  const rows = document.getElementById('rows');
  msg.innerHTML = '';
  rows.innerHTML = '';

  try {
    const res = await fetch('/api/leads/?' + params.toString(), {
      headers: {'X-Admin-Key': key}
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();

    rows.innerHTML = data.map(l => `
      <tr>
        <td>${l.id}</td>
        <td>${l.created_at || ''}</td>
        <td>${l.channel || ''}</td>
        <td>${l.service || ''}</td>
        <td>${l.city || ''}</td>
        <td>${l.urgency || ''}</td>
        <td>${l.contact || ''}</td>
        <td>
          <select onchange="updateStatus(${l.id}, this.value)">
            ${['new','contacted','qualified','closed','lost'].map(s => `<option value="${s}" ${s === (l.status || 'new') ? 'selected' : ''}>${s}</option>`).join('')}
          </select>
        </td>
        <td>${(l.raw_text || '').slice(0, 180)}</td>
      </tr>
    `).join('');
  } catch (e) {
    msg.innerHTML = '<p class="error">' + e.message + '</p>';
  }
}

async function updateStatus(id, status) {
  const key = document.getElementById('key').value;
  const res = await fetch(`/api/leads/${id}/status?status=${encodeURIComponent(status)}`, {
    method: 'PATCH',
    headers: {'X-Admin-Key': key}
  });
  if (!res.ok) alert(await res.text());
}
</script>
</body>
</html>
"""
