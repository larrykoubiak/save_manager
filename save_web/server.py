"""Small WSGI interface; served by Waitress, without a frontend build toolchain."""

import base64
import hmac
from importlib.resources import files
import json
import logging
from urllib.parse import urlsplit
from save_core.save_copy import CopyApplyError
from .icons import IconCache


class Application:
    def __init__(self, service, token=''):
        self.service = service
        self.token = token
        self.icons = IconCache(service)

    def __call__(self, environ, start_response):
        try:
            status, payload, mime, extra = self.dispatch(environ)
        except LookupError as error:
            status, payload, mime, extra = 404, {'error': str(error)}, 'application/json', []
        except (ValueError, TypeError) as error:
            status, payload, mime, extra = 400, {'error': str(error)}, 'application/json', []
        except (OSError, CopyApplyError) as error:
            status, payload, mime, extra = 409, {'error': str(error)}, 'application/json', []
        except Exception:
            logging.exception('Web request failed')
            status, payload, mime, extra = 500, {'error': 'Operation failed. Check the server log and transfer history.'}, 'application/json', []
        if mime == 'application/json':
            payload = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers = [('Content-Type', mime + ('; charset=utf-8' if mime.startswith(('text/', 'application/json', 'application/javascript')) else '')),
                   ('Content-Length', str(len(payload))), ('Cache-Control', 'no-store'),
                   ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'same-origin'),
                   ('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
        reasons = {200: 'OK', 201: 'Created', 202: 'Accepted', 400: 'Bad Request', 401: 'Unauthorized',
                   403: 'Forbidden', 404: 'Not Found', 405: 'Method Not Allowed', 409: 'Conflict', 500: 'Internal Server Error'}
        overridden = {key.lower() for key, _ in extra}
        start_response(f'{status} {reasons[status]}', [item for item in headers if item[0].lower() not in overridden] + extra)
        return [payload]

    def authorized(self, environ):
        if not self.token:
            return True
        try:
            scheme, credentials = environ.get('HTTP_AUTHORIZATION', '').split(' ', 1)
            if scheme.lower() != 'basic':
                return False
            _, password = base64.b64decode(credentials, validate=True).decode().split(':', 1)
            return hmac.compare_digest(password.encode(), self.token.encode())
        except (ValueError, UnicodeError):
            return False

    def dispatch(self, environ):
        path = environ.get('PATH_INFO', '/')
        method = environ['REQUEST_METHOD']

        def response(status, payload):
            return status, payload, 'application/json', []

        if path == '/healthz' and method == 'GET':
            self.service.db.rows('SELECT 1')
            return response(200, {'status': 'ok'})
        if not self.authorized(environ):
            return 401, {'error': 'Authentication required.'}, 'application/json', [('WWW-Authenticate', 'Basic realm="Save Manager", charset="UTF-8"')]
        if method not in ('GET', 'POST', 'PUT', 'DELETE'):
            return response(405, {'error': 'Method not allowed.'})
        body = {}
        if method != 'GET':
            # Custom header + same-origin policy prevent drive-by writes, including on localhost.
            if environ.get('HTTP_X_SAVE_MANAGER') != '1':
                return response(403, {'error': 'X-Save-Manager: 1 is required for mutations.'})
            origin = environ.get('HTTP_ORIGIN')
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme not in ('http', 'https') or parsed.netloc != environ.get('HTTP_HOST'):
                    return response(403, {'error': 'Cross-origin writes are not allowed.'})
            if environ.get('CONTENT_TYPE', '').split(';')[0] != 'application/json':
                raise ValueError('Use Content-Type: application/json.')
            length = int(environ.get('CONTENT_LENGTH') or 0)
            if not 0 < length <= 65536:
                raise ValueError('JSON body must contain 1–65536 bytes.')
            body = json.loads(environ['wsgi.input'].read(length))
            if not isinstance(body, dict):
                raise ValueError('JSON body must be an object.')
        if path in ('/', '/app.js', '/style.css') and method == 'GET':
            name, mime = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'application/javascript'),
                          '/style.css': ('style.css', 'text/css')}[path]
            return 200, files('save_web').joinpath('static', name).read_bytes(), mime, []
        if path == '/api/inventory' and method == 'GET':
            return response(200, self.service.inventory())
        if path.startswith('/api/icons/') and path.endswith('.gif') and method == 'GET':
            from urllib.parse import parse_qs
            key = path[len('/api/icons/'):-4]
            animated = parse_qs(environ.get('QUERY_STRING', '')).get('static') != ['1']
            return 200, self.icons.get(key, animated), 'image/gif', [('Cache-Control', 'private, max-age=3600')]
        if path == '/api/devices' and method == 'POST':
            return response(201, self.service.create_device(body))
        parts = path.strip('/').split('/')
        if len(parts) >= 3 and parts[:2] == ['api', 'devices']:
            device_id = int(parts[2])
            if len(parts) == 3 and method == 'PUT':
                return response(200, self.service.update_device(device_id, body))
            if len(parts) == 4 and parts[3] == 'locations' and method == 'POST':
                return response(201, self.service.save_location(device_id, body))
            if len(parts) == 5 and parts[3] == 'locations' and method == 'PUT':
                return response(200, self.service.save_location(device_id, body, int(parts[4])))
            if len(parts) == 5 and parts[3] == 'locations' and method == 'DELETE':
                return response(200, self.service.delete_location(device_id, int(parts[4])))
            if len(parts) == 4 and parts[3] == 'scan' and method == 'POST':
                return response(202, self.service.queue_scan(device_id))
        if path == '/api/transfers/preview' and method == 'POST':
            return response(201, self.service.preview(body))
        if path == '/api/sync/groups' and method == 'POST':
            return response(201, self.service.sync.configure(body))
        if len(parts) >= 4 and parts[:3] == ['api', 'sync', 'groups']:
            if len(parts) == 4 and method == 'PUT':
                return response(200, self.service.sync.set_enabled(int(parts[3]), body))
            if len(parts) == 5 and parts[4] == 'resolve' and method == 'POST':
                return response(200, self.service.sync.resolve(int(parts[3]), body))
            if len(parts) == 5 and parts[4] == 'retry' and method == 'POST':
                return response(200, self.service.sync.retry(int(parts[3])))
        if len(parts) == 4 and parts[:2] == ['api', 'transfers']:
            if parts[3] == 'apply' and method == 'POST':
                return response(200, self.service.apply(parts[2], body))
            if parts[3] == 'cancel' and method == 'POST':
                self.service.cancel_preview(parts[2])
                return response(200, {'status': 'cancelled'})
        return response(404, {'error': 'Route not found.'})
