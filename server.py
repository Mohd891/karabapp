#!/usr/bin/env python3
"""Karab web UI and image analysis API, with server-side Gemini credential."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from collections import defaultdict, deque
from urllib import request, error
from urllib.parse import urlparse
import os, json, base64, binascii, time, threading
from vision_estimator import analyze_pixels

ROOT = Path(__file__).resolve().parent
PROMPT = (ROOT / 'gemini_prompt.txt').read_text(encoding='utf-8')
SAMPLE_IMAGES = {
    '822508f7-207b-4d94-9b67-01d1f83443d6.JPG': 'https://d2ol7oe51mr4n9.cloudfront.net/user_3Js0ef5X6VEXugCB0S2ySgXrfMW/b8e9a9ef-8c52-4e60-84ee-fc8ad1730306.jpg',
    '07204f96-db2d-49e7-89fd-9f6ce4cae45f.JPG': 'https://d2ol7oe51mr4n9.cloudfront.net/user_3Js0ef5X6VEXugCB0S2ySgXrfMW/33c5e965-19ab-403d-a48f-a155df1a5239.jpg',
    'bfddac2a-291b-4212-ab78-07bc929a9291.JPG': 'https://d2ol7oe51mr4n9.cloudfront.net/user_3Js0ef5X6VEXugCB0S2ySgXrfMW/25f3b48a-369f-41d7-a600-a621afc1cb78.jpg'
}
SAMPLE_CACHE = {}

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
            return self.json_response(200, {'status':'ok','vision_estimator_active':True,'gemini_configured':bool(api_key())})
        if path == '/api/test':
            return self.json_response(200, {'status':'ok','engine':'image-pixel-analysis'})
        sample = SAMPLE_IMAGES.get(path.lstrip('/'))
        if sample:
            try:
                if sample not in SAMPLE_CACHE:
                    with request.urlopen(sample,timeout=12) as resp:
                        data = resp.read(500000)
                    if len(data)<50000 or len(data)>499999:
                        raise ValueError('sample image size')
                    SAMPLE_CACHE[sample]=data
                data=SAMPLE_CACHE[sample]
                self.send_response(200)
                self.send_header('Content-Type','image/jpeg')
                self.send_header('Content-Length',str(len(data)))
                self.send_header('Cache-Control','public, max-age=3600')
                self.end_headers()
                self.wfile.write(data)
                return
            except Exception as exc:
                print('Sample image fetch failure:',str(exc)[:120],flush=True)
                return self.send_error(503,'Sample temporarily unavailable')
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
        try:
            result = analyze_pixels(img_bytes, obj.get('dimensions'), obj.get('material_hint'))
            return self.json_response(200, {
                'text':json.dumps(result, ensure_ascii=False),
                'analysis_method':result['analysis_method'],
                'demo_mode':False
            })
        except ValueError as exc:
            return self.json_response(400, {'error':str(exc)})
        except Exception as exc:
            print('Visual analysis failed:',repr(exc),flush=True)
            return self.json_response(500, {'error':'حدث خطأ أثناء قراءة الصورة. جرّب صورة JPG أخرى.'})

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
    try:
        from PIL import Image
        from io import BytesIO
        img = Image.new('RGB',(128,128),(156,102,58))
        buf = BytesIO(); img.save(buf,format='JPEG')
        test = analyze_pixels(buf.getvalue())
        print('Pixel analyzer self-check OK:',test['detected_materials'][0],flush=True)
    except Exception as exc:
        print('Pixel analyzer self-check FAILED:',repr(exc),flush=True)
    app.serve_forever()
