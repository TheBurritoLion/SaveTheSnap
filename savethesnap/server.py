"""Loopback-only UI. The server has no outbound network client."""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import core


def serve(source=None, output=None, demo=False, open_browser=True, port=0):
    token = secrets.token_urlsafe(32)
    state = {'inventory': None, 'scan_id': None, 'next_index': 0, 'output': str(output or (Path.home() / 'SaveTheSnap-output')),
             'source': str(source or ''), 'demo': demo, 'busy': False, 'job': None, 'last_run': None}
    lock = threading.Lock()
    server = None

    def public_state():
        inv = state['inventory']
        return {'source': state['source'], 'output': state['output'], 'demo': demo,
                'busy': state['busy'], 'scan_id': state['scan_id'], 'job': state['job'], 'summary': inv['summary'] if inv else None,
                'issues': inv['issues'] if inv else [], 'last_run': state['last_run'],
                'prepared': state['next_index'], 'remaining': max(0, len(inv['items']) - state['next_index']) if inv else 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log tokens, paths, signed URLs, or media metadata.

        def authorized(self):
            port_value = server.server_address[1]
            expected = f'127.0.0.1:{port_value}'
            if self.headers.get('Host') != expected:
                return False
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + expected:
                return False
            return hmac.compare_digest(self.headers.get('X-SaveTheSnap', ''), token)

        def send(self, status, body, content_type='application/json'):
            data = json.dumps(body).encode() if content_type == 'application/json' else body
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/':
                # Token is carried in the fragment and never sent in a URL request.
                self.send(200, Path(__file__).with_name('web.html').read_bytes(), 'text/html; charset=utf-8')
            elif path == '/api/state' and self.authorized():
                with lock:
                    self.send(200, public_state())
            else:
                self.send(403, {'error': 'Reopen the app from its launcher.'})

        def do_POST(self):
            if not self.authorized():
                self.send(403, {'error': 'Request denied.'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384:
                    raise ValueError('Invalid request size.')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('Invalid request.')
                action = urlsplit(self.path).path
                if action == '/api/review':
                    with lock:
                        run = state['last_run']
                    if not run:
                        raise ValueError('Prepare a batch first.')
                    webbrowser.open((Path(run['folder']) / 'review.html').as_uri())
                    self.send(200, {'ok': True})
                    return
                if action == '/api/stop':
                    with lock:
                        if state['busy']:
                            raise ValueError('Wait for the current batch to finish before stopping.')
                    self.send(200, {'ok': True})
                    threading.Thread(target=server.shutdown, daemon=True).start()
                    return
                if action not in {'/api/scan', '/api/prepare'}:
                    self.send(404, {'error': 'Unknown action.'})
                    return
                with lock:
                    if state['busy']:
                        raise ValueError('The current operation is still running.')
                    if action == '/api/prepare' and not state['inventory']:
                        raise ValueError('Scan an export first.')
                    if action == '/api/prepare' and (data.get('scan_id') != state['scan_id'] or
                            core.safe_path(data.get('source', '')) != core.safe_path(state['source'])):
                        raise ValueError('Source selection changed. Scan the new selection before preparing.')
                    if action == '/api/scan':
                        state['inventory'] = None
                        state['scan_id'] = None
                        state['last_run'] = None
                        state['next_index'] = 0
                    state['busy'] = True
                    state['job'] = {'status': 'running', 'action': action.rsplit('/', 1)[1]}
                def work():
                    try:
                        if action == '/api/scan':
                            inv = core.scan(data.get('source', state['source']))
                            with lock:
                                state['inventory'] = inv
                                state['source'] = inv['source']
                                state['scan_id'] = secrets.token_urlsafe(16)
                                state['last_run'] = None
                        else:
                            destination = data.get('output', state['output'])
                            limit = int(data.get('limit', 20))
                            selection = [i['id'] for i in state['inventory']['items'][state['next_index']:state['next_index'] + limit]]
                            result = core.prepare(state['inventory'], destination, selected=selection, limit=limit)
                            with lock:
                                state['last_run'] = result
                                state['output'] = destination
                                state['next_index'] += result['count']
                        with lock:
                            state['job'] = {'status': 'done', 'action': action.rsplit('/', 1)[1]}
                    except Exception as exc:
                        with lock:
                            # Expected validation errors are deliberately path-free.
                            state['job'] = {'status': 'error', 'message': str(exc) if isinstance(exc, ValueError) else 'Operation failed. Originals are intact; inspect your local setup.'}
                    finally:
                        with lock:
                            state['busy'] = False
                threading.Thread(target=work, daemon=True).start()
                self.send(202, {'ok': True})
            except (ValueError, TypeError, json.JSONDecodeError):
                self.send(400, {'error': 'Check the selected folder and batch size, then try again.'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    url = f'http://127.0.0.1:{server.server_address[1]}/#{token}'
    if source:
        state['inventory'] = core.scan(source)
        state['scan_id'] = secrets.token_urlsafe(16)
    # URL is needed only by the owner/launcher; HTTP requests never log it.
    print('SaveTheSnap is running locally. Close with Ctrl+C or Stop app.', flush=True)
    print(url, flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
