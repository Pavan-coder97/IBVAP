"""Light proxy app for Vercel deployment.

This app serves the static frontend and proxies all `/api/*` and
streaming endpoints to an external ML backend configured via the
`ML_BACKEND_URL` environment variable.

Usage: set `ML_BACKEND_URL` to the full URL of your deployed ML server
e.g. https://ml.example.com
"""
import os
import requests
from flask import Flask, request, Response, send_from_directory
from flask_cors import CORS

ML_BACKEND = os.environ.get('ML_BACKEND_URL')
if ML_BACKEND and ML_BACKEND.endswith('/'):
    ML_BACKEND = ML_BACKEND[:-1]

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)


@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


def _proxy_request(path):
    if not ML_BACKEND:
        return ("ML backend not configured", 502)

    url = f"{ML_BACKEND}/{path}"
    method = request.method
    headers = {k: v for k, v in request.headers if k.lower() not in ['host', 'content-length']}

    try:
        if method in ('GET', 'DELETE'):
            r = requests.request(method, url, params=request.args, headers=headers, stream=True, timeout=60)
            return Response(r.raw, status=r.status_code, headers={k: v for k, v in r.headers.items() if k.lower() != 'transfer-encoding'})
        else:
            # POST/PUT/PATCH — forward data and files
            data = request.get_data()
            files = None
            if request.files:
                files = {k: (f.filename, f.stream, f.mimetype) for k, f in request.files.items()}
                r = requests.request(method, url, params=request.args, headers=headers, data=request.form, files=files, timeout=300, stream=True)
            else:
                r = requests.request(method, url, params=request.args, headers=headers, data=data, timeout=300, stream=True)
            return Response(r.raw, status=r.status_code, headers={k: v for k, v in r.headers.items() if k.lower() != 'transfer-encoding'})
    except requests.RequestException as exc:
        return (f"Upstream error: {exc}", 502)


@app.route('/api/<path:subpath>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
def api_proxy(subpath):
    return _proxy_request(f"api/{subpath}")


@app.route('/video_feed')
@app.route('/video_feed/<path:subpath>')
def video_proxy(subpath=''):
    target = 'video_feed' + (('/' + subpath) if subpath else '')
    return _proxy_request(target)


@app.route('/events')
@app.route('/events/<path:subpath>')
def events_proxy(subpath=''):
    target = 'events' + (('/' + subpath) if subpath else '')
    return _proxy_request(target)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
