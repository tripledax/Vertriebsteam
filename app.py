from flask import Flask, request, jsonify, redirect, session
from flask_cors import CORS
import uuid, time, os, base64
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'iee-solar-secret-2025')
CORS(app, origins="*", supports_credentials=True)

# Google OAuth config - loaded from environment variables
def get_client_config():
    return {
        "web": {
            "client_id":     os.environ.get('GOOGLE_CLIENT_ID', ''),
            "client_secret": os.environ.get('GOOGLE_CLIENT_SECRET', ''),
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": ["https://iee-signature.onrender.com/oauth/callback"]
        }
    }

SCOPES       = ['https://www.googleapis.com/auth/drive.file']
REDIRECT_URI = 'https://iee-signature.onrender.com/oauth/callback'

sessions     = {}
drive_tokens = {}
SESSION_TTL  = 600

def cleanup():
    now = time.time()
    expired = [k for k,v in sessions.items() if now - v['created'] > SESSION_TTL]
    for k in expired: del sessions[k]

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'drive_connected': 'default' in drive_tokens})

@app.route('/auth/google')
def auth_google():
    flow = Flow.from_client_config(get_client_config(), scopes=SCOPES)
    flow.redirect_uri = REDIRECT_URI
    auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')
    session['oauth_state'] = state
    return jsonify({'auth_url': auth_url})

@app.route('/oauth/callback')
def oauth_callback():
    flow = Flow.from_client_config(get_client_config(), scopes=SCOPES,
                                    state=request.args.get('state'))
    flow.redirect_uri = REDIRECT_URI
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    drive_tokens['default'] = {
        'token':          creds.token,
        'refresh_token':  creds.refresh_token,
        'token_uri':      creds.token_uri,
        'client_id':      creds.client_id,
        'client_secret':  creds.client_secret,
        'scopes':         list(creds.scopes) if creds.scopes else SCOPES
    }
    return '''<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Verbunden!</title>
<style>body{font-family:sans-serif;display:flex;justify-content:center;align-items:center;
min-height:100vh;background:#f0f4fc;margin:0;}
.card{background:#fff;padding:40px;border-radius:16px;box-shadow:0 4px 20px rgba(0,0,0,.1);
text-align:center;max-width:400px;}
h1{color:#16a34a;font-size:28px;margin-bottom:12px;}
p{color:#6b7280;font-size:15px;line-height:1.6;}</style>
</head><body><div class="card">
<h1>✅ Google Drive verbunden!</h1>
<p>PDFs werden ab jetzt automatisch in Google Drive gespeichert.<br><br>
Dieses Fenster kann geschlossen werden.</p>
</div></body></html>'''

@app.route('/auth/status')
def auth_status():
    return jsonify({'connected': 'default' in drive_tokens})

def get_drive_service():
    if 'default' not in drive_tokens: return None
    t = drive_tokens['default']
    creds = Credentials(token=t['token'], refresh_token=t.get('refresh_token'),
                        token_uri=t['token_uri'], client_id=t['client_id'],
                        client_secret=t['client_secret'], scopes=t['scopes'])
    return build('drive', 'v3', credentials=creds)

def get_or_create_folder(service, name, parent_id=None):
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id: q += f" and '{parent_id}' in parents"
    results = service.files().list(q=q, fields='files(id)').execute()
    files = results.get('files', [])
    if files: return files[0]['id']
    meta = {'name': name, 'mimeType': 'application/vnd.google-apps.folder'}
    if parent_id: meta['parents'] = [parent_id]
    return service.files().create(body=meta, fields='id').execute()['id']

def upload_pdf_to_drive(service, pdf_b64, filename, customer_name, date_str):
    try:
        root_id   = get_or_create_folder(service, 'IEE Solar')
        kunden_id = get_or_create_folder(service, 'Kunden', root_id)
        cust_id   = get_or_create_folder(service, customer_name, kunden_id)
        date_id   = get_or_create_folder(service, date_str, cust_id)
        pdf_bytes = base64.b64decode(pdf_b64)
        media     = MediaInMemoryUpload(pdf_bytes, mimetype='application/pdf')
        meta      = {'name': filename, 'parents': [date_id]}
        f = service.files().create(body=meta, media_body=media, fields='webViewLink').execute()
        return f.get('webViewLink', '')
    except Exception as e:
        print(f'Drive error: {e}')
        return None

@app.route('/upload-pdfs', methods=['POST'])
def upload_pdfs():
    service = get_drive_service()
    if not service:
        return jsonify({'error': 'Drive nicht verbunden', 'connected': False}), 401
    data          = request.json
    customer_name = data.get('customer_name', 'Unbekannt')
    date_str      = data.get('date', time.strftime('%Y-%m-%d'))
    links = []
    for pdf in data.get('pdfs', []):
        link = upload_pdf_to_drive(service, pdf['b64'], pdf['filename'], customer_name, date_str)
        if link: links.append({'filename': pdf['filename'], 'link': link})
    return jsonify({'success': True, 'files': links, 'count': len(links)})

@app.route('/create-session', methods=['POST'])
def create_session():
    cleanup()
    session_id = str(uuid.uuid4())
    data = request.json or {}
    sessions[session_id] = {
        'created': time.time(), 'status': 'pending',
        'customer_name': data.get('customer_name', ''), 'signature': None
    }
    return jsonify({'session_id': session_id})

@app.route('/sign/<session_id>')
def sign_page(session_id):
    if session_id not in sessions:
        return "Link ungültig oder abgelaufen.", 404
    customer = sessions[session_id].get('customer_name', 'Kunde')
    return f'''<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,user-scalable=no">
<title>Unterschrift – IEE Solar</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,sans-serif;background:#f0f4fc;min-height:100vh}}
.wrap{{max-width:480px;margin:0 auto;padding:20px}}
.card{{background:#fff;border-radius:16px;padding:24px;box-shadow:0 4px 20px rgba(0,0,0,.1)}}
.logo{{background:#1a4fc4;color:#fff;font-weight:800;font-size:18px;width:44px;height:44px;
border-radius:10px;display:flex;align-items:center;justify-content:center;margin-bottom:16px}}
h1{{font-size:20px;color:#111827;margin-bottom:6px}}
p{{font-size:14px;color:#6b7280;margin-bottom:20px;line-height:1.5}}
.cw{{border:2px solid #1a4fc4;border-radius:10px;background:#fff;margin-bottom:16px}}
canvas{{display:block;touch-action:none;cursor:crosshair;border-radius:8px}}
.hint{{font-size:12px;color:#9ca3af;text-align:center;margin-bottom:16px}}
.btn{{width:100%;padding:16px;border:none;border-radius:10px;font-size:16px;font-weight:700;
cursor:pointer;margin-bottom:10px;font-family:inherit}}
.bp{{background:#1a4fc4;color:#fff}}.bc{{background:#f3f4f6;color:#374151}}
.ok{{background:#dcfce7;color:#15803d;border-radius:10px;padding:20px;text-align:center;display:none}}
</style></head>
<body><div class="wrap">
  <div class="card" id="sc">
    <div class="logo">IEE</div>
    <h1>Digitale Unterschrift</h1>
    <p>Hallo <strong>{customer}</strong>, bitte mit dem Finger im Feld unten unterschreiben.</p>
    <div class="cw"><canvas id="sig" width="440" height="180"></canvas></div>
    <div class="hint">👆 Mit Finger unterschreiben</div>
    <button class="btn bp" onclick="sub()">✅ Unterschrift bestätigen</button>
    <button class="btn bc" onclick="clr()">🗑️ Löschen & neu</button>
  </div>
  <div class="ok" id="ok"><h2 style="font-size:22px;margin-bottom:8px">✅ Vielen Dank!</h2>
  <p>Unterschrift übermittelt. Fenster kann geschlossen werden.</p></div>
</div>
<script>
const cv=document.getElementById('sig'),ctx=cv.getContext('2d');
const W=cv.width,H=cv.height,r=window.devicePixelRatio||1;
cv.width=W*r;cv.height=H*r;cv.style.width=W+'px';cv.style.height=H+'px';
ctx.scale(r,r);ctx.strokeStyle='#1a1a2e';ctx.lineWidth=2.5;ctx.lineCap='round';ctx.lineJoin='round';
let d=false,e=true;
function p(ev){{const rc=cv.getBoundingClientRect(),t=ev.touches?ev.touches[0]:ev;
return[(t.clientX-rc.left)/(rc.width/W),(t.clientY-rc.top)/(rc.height/H)];}}
cv.addEventListener('mousedown',ev=>{{d=true;ctx.beginPath();ctx.moveTo(...p(ev));}});
cv.addEventListener('mousemove',ev=>{{if(!d)return;e=false;ctx.lineTo(...p(ev));ctx.stroke();ctx.beginPath();ctx.moveTo(...p(ev));}});
cv.addEventListener('mouseup',()=>d=false);
cv.addEventListener('touchstart',ev=>{{ev.preventDefault();d=true;ctx.beginPath();ctx.moveTo(...p(ev));}},{{passive:false}});
cv.addEventListener('touchmove',ev=>{{ev.preventDefault();if(!d)return;e=false;ctx.lineTo(...p(ev));ctx.stroke();ctx.beginPath();ctx.moveTo(...p(ev));}},{{passive:false}});
cv.addEventListener('touchend',()=>d=false);
function clr(){{ctx.clearRect(0,0,W*r,H*r);e=true;}}
async function sub(){{
  if(e){{alert('Bitte erst unterschreiben!');return;}}
  const res=await fetch('/submit-signature/{session_id}',{{method:'POST',
    headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{signature:cv.toDataURL('image/png').split(',')[1]}})}});
  if(res.ok){{document.getElementById('sc').style.display='none';
  document.getElementById('ok').style.display='block';}}
}}
</script></body></html>'''

@app.route('/submit-signature/<session_id>', methods=['POST'])
def submit_signature(session_id):
    if session_id not in sessions:
        return jsonify({'error': 'Session not found'}), 404
    data = request.json
    sessions[session_id].update({'signature': data.get('signature'),
                                  'status': 'signed', 'signed_at': time.time()})
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
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
