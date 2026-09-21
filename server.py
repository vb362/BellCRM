#!/usr/bin/env python3
"""Start Bellhaven: .venv/bin/python server.py (http://localhost:8000)."""
import argparse
from contextlib import closing, nullcontext
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import quote, urlsplit

from normalize_data import DATABASES, open_database
from run_pipeline import start_pipeline, run_pipeline
import review_service as review

ROOT = Path(__file__).resolve().parent


def local_crm_link_base():
    """Build credential-bearing links only at runtime for the local browser.

    This does not call the CRM API. Never write this value into static assets,
    database snapshots, logs, or public exports.
    """
    token = os.environ.get('BELLHAVEN_API_TOKEN', '').strip()
    if not token:
        return None
    return 'https://analyst-assessment-production.up.railway.app/crm/' + quote(token, safe='') + '/accounts/'


def load_local_config(path=None):
    """Load the local CRM credential at startup; an environment override wins."""
    if os.environ.get('BELLHAVEN_API_TOKEN', '').strip():
        return
    path = Path(path) if path is not None else ROOT / '.env'
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        key, separator, value = line.strip().partition('=')
        if separator and key.strip() == 'BELLHAVEN_API_TOKEN':
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if value:
                os.environ['BELLHAVEN_API_TOKEN'] = value
            return


class Application:
    def __init__(self, database, mode="test", production=None):
        self.database = Path(database)
        self.mode = mode
        self.production = production
        self.lock = threading.Lock()
        self.worker = None
        with closing(open_database(self.database)) as c:
            if c.execute('PRAGMA user_version').fetchone()[0] not in (3, 4):
                raise ValueError('Schema version 3 is required')
            active = c.execute("SELECT 1 FROM runs WHERE run_type='pipeline' AND status='running'").fetchone()
        if not active and not (production and (production.state() or {}).get('status') in ('running','paused')):
            review.complete_existing_matches(self.database)

    def state(self):
        result = review.read_state(self.database)
        result['mode'] = self.mode
        result['production'] = self.production.state() if self.production else None
        result['crm_link_base'] = local_crm_link_base()
        return result

    def start(self):
        with self.lock:
            with closing(open_database(self.database)) as c:
                active = c.execute("SELECT run_id FROM runs WHERE run_type='pipeline' AND status='running' LIMIT 1").fetchone()
            if active:
                return active['run_id']
            run_id = start_pipeline(self.database)
            def work():
                try:
                    run_pipeline(self.database, run_id)
                except Exception as error:
                    print(f'Pipeline stopped: {error}', flush=True)
            self.worker = threading.Thread(target=work, name='bellhaven-pipeline', daemon=True)
            self.worker.start()
            return run_id

    def reset(self, baseline, production_database):
        if self.mode != 'test' or self.production:
            raise ValueError('Reset is available only in Test mode.')
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise ValueError('A run is active. Wait for it to finish before resetting Test mode.')
            from reset_demo import reset_demo
            backup = reset_demo(self.database, baseline, production_database)
            result = self.state()
            result['reset'] = {'backup_file': backup.name}
            return result


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'mistral-replica/dist'), **kwargs)

    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        return self.headers.get('X-Bellhaven-Session') in self.server.sessions

    def local_request(self):
        allowed = {f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}'}
        if self.headers.get('Host') not in allowed:
            return False
        origin = self.headers.get('Origin')
        return not origin or origin in {'http://' + h for h in allowed}

    def do_GET(self):
        if not self.local_request():
            return self.send_json(403, {'error': 'Use the local server address'})
        if urlsplit(self.path).path.startswith('/api/'):
            if not self.authorized():
                return self.send_json(401, {'error': 'Choose a mode first'})
            if self.path != '/api/state':
                return self.send_json(404, {'error': 'Not found'})
            try:
                return self.send_json(200, self.server.sessions[self.headers.get('X-Bellhaven-Session')].state())
            except Exception as error:
                return self.send_json(500, {'error': str(error)})
        if urlsplit(self.path).path.endswith('/') and urlsplit(self.path).path != '/':
            return self.send_error(404)
        super().do_GET()

    def do_POST(self):
        try:
            if not self.local_request() or self.headers.get('Content-Type') != 'application/json':
                return self.send_json(403, {'error': 'Expected a local JSON request'})
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1_000_000:
                raise ValueError('Invalid request size')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('Expected an object')
            if self.path == '/api/session':
                mode = body.get('mode')
                if mode not in ('test', 'production'):
                    raise ValueError('Choose Test or Production mode')
                if mode == 'production':
                    with self.server.mode_lock:
                        if self.server.production_app is None:
                            from production_api import ProductionService
                            service = ProductionService(self.server.production_database, self.server.production_client)
                            self.server.production_app = Application(service.database, mode='production', production=service)
                        app = self.server.production_app
                    saved = app.production.state()
                    if saved and saved['status'] in ('running', 'paused'):
                        run_id = None  # Resume the ledger before any refresh/pipeline.
                    else:
                        with closing(open_database(app.database)) as c:
                            active = c.execute("SELECT 1 FROM runs WHERE run_type='pipeline' AND status='running'").fetchone()
                        if not active:
                            app.production.refresh()
                        run_id = app.start()
                else:
                    app = self.server.app
                    run_id = app.start()
                token = secrets.token_urlsafe(32)
                self.server.sessions[token] = app
                return self.send_json(200, dict(token=token, run_id=run_id, mode=mode))
            if not self.authorized():
                return self.send_json(401, {'error': 'Choose a mode first'})
            app = self.server.sessions[self.headers.get('X-Bellhaven-Session')]
            if self.path == '/api/test/reset':
                if body.get('confirm') is not True:
                    raise ValueError('Confirm the reset before restoring Test mode.')
                return self.send_json(200, app.reset(self.server.baseline_database, self.server.production_database))
            if self.path == '/api/runs':
                if app.production:
                    app.production.refresh()
                return self.send_json(200, dict(run_id=app.start()))
            if self.path == '/api/production/preview':
                if not app.production:
                    raise ValueError('API submission is available only in Production mode')
                app.production.prepare(body.get('items'))
            elif self.path == '/api/production/cancel':
                if not app.production:
                    raise ValueError('Choose Production mode first')
                app.production.cancel(body.get('plan_id'), body.get('digest'))
            elif self.path == '/api/decisions/submit':
                if app.production:
                    if body.get('confirm') is not True:
                        raise ValueError('Review the saved API calls and explicitly confirm execution')
                    app.production.execute(body.get('plan_id'), body.get('digest'), body.get('recovery_ids'))
                else:
                    review.submit(app.database, body.get('items'))
            elif self.path in ('/api/decisions/stage', '/api/decisions/remove'):
                with app.production.exclusive() if app.production else nullcontext():
                    if self.path.endswith('/stage'):
                        review.stage(app.database, body.get('items'))
                    else:
                        review.unstage(app.database, body.get('proposal_id'), body.get('revision'))
            else:
                return self.send_json(404, {'error': 'Not found'})
            self.send_json(200, app.state())
        except (ValueError, TypeError, KeyError) as error:
            self.send_json(409, {'error': str(error)})
        except Exception as error:
            self.send_json(500, {'error': str(error)})

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def log_message(self, fmt, *args):
        if self.path != '/api/state':
            super().log_message(fmt, *args)


def make_server(port=8000, database=None, production_database=None, production_client=None, baseline_database=None):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.app = Application(database or DATABASES['demo'])
    server.sessions = {}
    server.mode_lock = threading.Lock()
    server.production_app = None
    server.production_database = production_database or DATABASES['production']
    server.production_client = production_client
    server.baseline_database = baseline_database or ROOT / 'data/start.sqlite'
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    load_local_config()
    server = make_server(args.port)
    print(f'Bellhaven: http://localhost:{server.server_port}\nDatabase: {server.app.database}\nPress Ctrl+C to stop.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        for app in (server.app, server.production_app):
            if app and app.worker and app.worker.is_alive():
                print('Waiting for the active pipeline to finish safely…', flush=True)
                app.worker.join()


if __name__ == '__main__':
    main()
