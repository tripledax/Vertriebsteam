from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import uuid, time, os, base64

app = Flask(__name__)
CORS(app, origins="*")

# In-memory store: {session_id: {signature_b64, timestamp, status}}
sessions = {}
SESSION_TTL = 600  # 10 minutes

def cleanup():
    """Remove expired sessions"""
    now = time.time()
    expired = [k for k, v in sessions.items() if now - v['created'] > SESSION_TTL]
    for k in expired:
        del sessions[k]

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

@app.route('/create-session', methods=['POST'])
def create_session():
    cleanup()
    session_id = str(uuid.uuid4())
    data = request.json or {}
    sessions[session_id] = {
        'created': time.time(),
        'status': 'pending',
        'customer_name': data.get('customer_name', ''),
        'signature': None
    }
    return jsonify({'session_id': session_id})

@app.route('/sign/<session_id>', methods=['GET'])
def sign_page(session_id):
    if session_id not in sessions:
        return "Link ungültig oder abgelaufen.", 404
    customer = sessions[session_id].get('customer_name', 'Kunde')
    return f'''<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>Unterschrift – IEE Solar</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, sans-serif; background: #f0f4fc; min-height: 100vh; }}
.wrap {{ max-width: 480px; margin: 0 auto; padding: 20px; }}
.card {{ background: #fff; border-radius: 16px; padding: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }}
.logo {{ background: #1a4fc4; color: #fff; font-weight: 800; font-size: 18px; width: 44px; height: 44px; border-radius: 10px; display: flex; align-items: center; justify-content: center; margin-bottom: 16px; }}
h1 {{ font-size: 20px; color: #111827; margin-bottom: 6px; }}
p {{ font-size: 14px; color: #6b7280; margin-bottom: 20px; line-height: 1.5; }}
.canvas-wrap {{ border: 2px solid #1a4fc4; border-radius: 10px; background: #fff; position: relative; margin-bottom: 16px; }}
canvas {{ display: block; touch-action: none; cursor: crosshair; border-radius: 8px; }}
.hint {{ font-size: 12px; color: #9ca3af; text-align: center; margin-bottom: 16px; }}
.btn {{ width: 100%; padding: 16px; border: none; border-radius: 10px; font-size: 16px; font-weight: 700; cursor: pointer; margin-bottom: 10px; font-family: inherit; }}
.btn-primary {{ background: #1a4fc4; color: #fff; }}
.btn-clear {{ background: #f3f4f6; color: #374151; }}
.success {{ background: #dcfce7; color: #15803d; border-radius: 10px; padding: 20px; text-align: center; display: none; }}
.success h2 {{ font-size: 22px; margin-bottom: 8px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card" id="sign-card">
    <div class="logo">IEE</div>
    <h1>Digitale Unterschrift</h1>
    <p>Hallo <strong>{customer}</strong>, bitte unterschreiben Sie im Feld unten mit Ihrem Finger.</p>
    <div class="canvas-wrap">
      <canvas id="sig" width="440" height="180"></canvas>
    </div>
    <div class="hint">👆 Mit Finger unterschreiben</div>
    <button class="btn btn-primary" onclick="submit()">✅ Unterschrift bestätigen</button>
    <button class="btn btn-clear" onclick="clear()">🗑️ Löschen & neu</button>
  </div>
  <div class="success" id="success-card">
    <h2>✅ Vielen Dank!</h2>
    <p>Ihre Unterschrift wurde erfolgreich übermittelt.<br>Sie können dieses Fenster schließen.</p>
  </div>
</div>
<script>
const canvas = document.getElementById('sig');
const ctx = canvas.getContext('2d');
const W = canvas.width, H = canvas.height;
let drawing = false, empty = true;

// Scale canvas for retina
const ratio = window.devicePixelRatio || 1;
canvas.width = W * ratio; canvas.height = H * ratio;
canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
ctx.scale(ratio, ratio);
ctx.strokeStyle = '#1a1a2e';
ctx.lineWidth = 2.5;
ctx.lineCap = 'round';
ctx.lineJoin = 'round';

function pos(e) {{
  const r = canvas.getBoundingClientRect();
  const t = e.touches ? e.touches[0] : e;
  return [(t.clientX - r.left) / (r.width / W), (t.clientY - r.top) / (r.height / H)];
}}

canvas.addEventListener('mousedown',  e => {{ drawing=true; ctx.beginPath(); ctx.moveTo(...pos(e)); }});
canvas.addEventListener('mousemove',  e => {{ if(!drawing) return; empty=false; ctx.lineTo(...pos(e)); ctx.stroke(); ctx.beginPath(); ctx.moveTo(...pos(e)); }});
canvas.addEventListener('mouseup',    e => drawing = false);
canvas.addEventListener('touchstart', e => {{ e.preventDefault(); drawing=true; ctx.beginPath(); ctx.moveTo(...pos(e)); }}, {{passive:false}});
canvas.addEventListener('touchmove',  e => {{ e.preventDefault(); if(!drawing) return; empty=false; ctx.lineTo(...pos(e)); ctx.stroke(); ctx.beginPath(); ctx.moveTo(...pos(e)); }}, {{passive:false}});
canvas.addEventListener('touchend',   e => drawing = false);

function clear() {{ ctx.clearRect(0,0,W*ratio,H*ratio); empty=true; }}

async function submit() {{
  if (empty) {{ alert('Bitte erst unterschreiben!'); return; }}
  const data = canvas.toDataURL('image/png').split(',')[1];
  const res = await fetch('/submit-signature/{session_id}', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{signature: data}})
  }});
  if (res.ok) {{
    document.getElementById('sign-card').style.display = 'none';
    document.getElementById('success-card').style.display = 'block';
  }}
}}
</script>
</body>
</html>'''

@app.route('/submit-signature/<session_id>', methods=['POST'])
def submit_signature(session_id):
    if session_id not in sessions:
        return jsonify({'error': 'Session not found'}), 404
    data = request.json
    sessions[session_id]['signature'] = data.get('signature')
    sessions[session_id]['status'] = 'signed'
    sessions[session_id]['signed_at'] = time.time()
    return jsonify({'success': True})

@app.route('/check-signature/<session_id>')
def check_signature(session_id):
    if session_id not in sessions:
        return jsonify({'status': 'expired'})
    s = sessions[session_id]
    if s['status'] == 'signed':
        return jsonify({'status': 'signed', 'signature': s['signature']})
    return jsonify({'status': 'pending'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
