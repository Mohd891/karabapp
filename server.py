#!/usr/bin/env python3
"""Karab web UI and image analysis API, with server-side Gemini credential."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from collections import defaultdict, deque
from urllib import request, error
from urllib.parse import urlparse
import os, json, base64, binascii, time, threading, hashlib

ROOT = Path(__file__).resolve().parent
PROMPT = (ROOT / 'gemini_prompt.txt').read_text(encoding='utf-8')
ALLOWED = {'index.html', 'carabapp.html', 'favicon.ico'} | {p.name for p in ROOT.glob('*.JPG')}
MODELS = ['gemini-2.5-flash', 'gemini-flash-latest']
MAX_BODY = 12 * 1024 * 1024
MAX_IMAGE = 8 * 1024 * 1024
RATE_LIMIT_PER_HOUR = 40
rates = defaultdict(deque)
rate_lock = threading.Lock()

def api_key():
    return os.environ.get('GEMINI_API_KEY', '').strip()

def gemini_request(url, payload=None):
    key = api_key()
    if not key:
        raise RuntimeError('مفتاح Gemini API غير مضبوط على السيرفر.')
    post_data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = request.Request(
        url, data=post_data,
        headers=({'Content-Type':'application/json','x-goog-api-key':key} if post_data is not None else {'x-goog-api-key':key}),
        method='POST' if post_data is not None else 'GET'
    )
    try:
        with request.urlopen(req, timeout=70) as response:
            return json.load(response)
    except error.HTTPError as exc:
        try: data = json.loads(exc.read(8192))
        except Exception: data = {}
        detail = str((data.get('error') or {}).get('message', 'Google API request failed'))
        raise ValueError('Google Gemini HTTP ' + str(exc.code) + ': ' + detail.replace(key, '[REDACTED]')[:300]) from None

def example_estimate(image_bytes):
    """Clearly-labeled offline demo, never a real computer vision measurement."""
    seed = int.from_bytes(hashlib.sha256(image_bytes).digest()[:4], 'big')
    # Sample numbers vary to illustrate the flow, NOT according to image content.
    total = 200 + (seed % 16) * 25
    palm = round(total * 0.64)
    base = total - palm
    return {
      'demo_mode': True,
      'demo_disclaimer': 'محاكاة تجريبية لعرض رحلة التطبيق فقط؛ لم تُحلّل الصورة بالذكاء الاصطناعي ولا يُستدل بهذه الأرقام على وزن المخلفات.',
      'detected_materials': ['مخلفات نخيل (افتراضية)', 'سعف وكرب (افتراضي)'],
      'scale_reference_used': 'لا يوجد مقياس مرجعي. القيم مثال توضيحي وليست قياسات من الصورة.',
      'analysis_breakdown': [
        {'material':'سعف (مثال)','estimation_method':'volumetric_pile','estimated_dimensions_or_count':'حجم افتراضي غير مستخرج من الصورة','estimated_volume_m3':round(palm/100, 1),'packing_or_moisture_notes':'كثافة افتراضية للعرض','estimated_weight_kg':palm},
        {'material':'كرب (مثال)','estimation_method':'volumetric_pile','estimated_dimensions_or_count':'حجم افتراضي غير مستخرج من الصورة','estimated_volume_m3':round(base/250, 1),'packing_or_moisture_notes':'كثافة افتراضية للعرض','estimated_weight_kg':base}
      ],
      'total_estimated_weight_kg':total,
      'confidence_interval_kg':{'min':round(total*0.4),'max':round(total*2.0)},
      'confidence_score':'Low'
    }

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def json_response(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/health':
            return self.json_response(200, {'status':'ok','gemini_configured':bool(api_key())})
        if path == '/api/test':
            if not api_key():
                return self.json_response(503, {'error':'GEMINI_API_KEY غير مضبوط في السيرفر.'})
            try:
                data = gemini_request('https://generativelanguage.googleapis.com/v1beta/models')
                models = [m.get('name','').removeprefix('models/') for m in data.get('models',[]) if 'generateContent' in m.get('supportedGenerationMethods',[])]
                return self.json_response(200, {'status':'ok', 'models':models[:15]})
            except Exception as exc:
                return self.json_response(502, {'error':str(exc)})
        if path == '/': path = '/index.html'
        if '..' in path or path.lstrip('/') not in ALLOWED:
            return self.send_error(404, 'Not Found')
        self.path = path
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != '/api/analyze':
            return self.json_response(404, {'error':'Not Found'})
        try: size = int(self.headers.get('Content-Length','0'))
        except ValueError: size = 0
        if not 0 < size <= MAX_BODY:
            return self.json_response(413, {'error':'حجم الطلب غير مناسب.'})
        try:
            obj = json.loads(self.rfile.read(size))
            mime = obj.get('mime_type','image/jpeg')
            if mime not in {'image/jpeg','image/png','image/webp','image/heic','image/heif'}:
                return self.json_response(400, {'error':'صيغة الصورة غير مدعومة.'})
            image = obj['image']
            img_bytes = base64.b64decode(image,validate=True)
            if not 20 <= len(img_bytes) <= MAX_IMAGE:
                return self.json_response(413, {'error':'حجم الصورة غير مناسب.'})
        except (ValueError, KeyError, TypeError, binascii.Error):
            return self.json_response(400, {'error':'الصورة المرسلة غير صالحة.'})
        client = self.client_address[0]
        with rate_lock:
            now = time.time()
            bucket = rates[client]
            while bucket and bucket[0] < now - 3600: bucket.popleft()
            if len(bucket) >= RATE_LIMIT_PER_HOUR:
                return self.json_response(429, {'error':'عدد محاولات التحليل تجاوز الحد التجريبي.'})
            bucket.append(now)
        if not api_key():
            return self.json_response(200, {'text':json.dumps(example_estimate(img_bytes),ensure_ascii=False),'demo_mode':True})
        payload = {
            'contents':[{'parts':[{'text':PROMPT},{'inline_data':{'mime_type':mime,'data':image}}]}],
            'generationConfig':{'response_mime_type':'application/json'}
        }
        failures = []
        for model in MODELS:
            try:
                output = gemini_request('https://generativelanguage.googleapis.com/v1beta/models/' + model + ':generateContent',payload)
                parts = (output.get('candidates') or [{}])[0].get('content',{}).get('parts',[])
                result = next((p.get('text') for p in parts if p.get('text')),None)
                if not result: raise ValueError('لم يرجع Gemini نتيجة مفهومة.')
                return self.json_response(200, {'model':model,'text':result})
            except Exception as exc:
                failures.append(str(exc))
                if any(x in str(exc) for x in ('401','403','429')): break
        return self.json_response(200, {'text':json.dumps(example_estimate(img_bytes),ensure_ascii=False),'demo_mode':True, 'api_error':'Gemini unavailable'})

def _startup_gemini_check():
    """One cheap auth check, logged without exposing the key."""
    try:
        data = gemini_request('https://generativelanguage.googleapis.com/v1beta/models')
        n = len(data.get('models', []))
        print('Gemini startup auth check OK; models returned:', n, flush=True)
    except Exception as exc:
        print('Gemini startup auth check failed:', str(exc)[:350], flush=True)

if __name__ == '__main__':
    port = int(os.environ.get('PORT','3000'))
    app = ThreadingHTTPServer(('0.0.0.0',port),Handler)
    print('Karab API serving on port',port,flush=True)
    if api_key(): threading.Thread(target=_startup_gemini_check,daemon=True).start()
    app.serve_forever()
