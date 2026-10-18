# كَرَب — نسخة اختبار الكاميرا وGemini API

تحتوي النسخة على **الواجهة كاملة، الصور الأربع، واختبار Python**، مع خادم Python يحمي مفتاح Gemini.

## التشغيل

1. ثبت Python 3.10 أو أحدث.
2. أنشئ ملف `.env` في نفس المجلد بمحتوى `GEMINI_API_KEY=...` (لا ترفعه إلى GitHub).
3. شغّل `python server.py`.
4. افتح `http://localhost:3000` للتجربة على نفس الكمبيوتر.
5. لفتح الكاميرا على جهاز آخر، انشر الخادم في Render (HTTPS تلقائي) ثم افتح الرابط من الهاتف.

## Render

ارفع محتويات هذا المجلد إلى مستودع GitHub، وأنشئ Web Service بنوع Python. 
- Build: `pip install -r requirements.txt`
- Start: `python server.py`
- Environment variable: `GEMINI_API_KEY` (سرّي — لا تضعه بملف JavaScript).
- Health: `/api/health`, Test: `/api/test`, Analyze: `/api/analyze`.

**مهم:** المفتاح القديم كان داخل `config.js` في الملف الأصلي؛ قم بإلغائه وإنشاء مفتاح جديد قبل النشر العلني.

التقديرات البصرية تجريبية وغير معتمدة للوزن التجاري؛ يلزم قياس ميداني للمعايرة والتأكد.