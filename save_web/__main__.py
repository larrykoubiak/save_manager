import argparse
import logging
import os
from pathlib import Path

from save_core.app_info import __version__
from .server import Application
from .service import Service


def main():
    parser = argparse.ArgumentParser(description='Save Manager device dashboard')
    parser.add_argument('--version', action='version', version=f'Save Manager Web {__version__}')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('SAVE_MANAGER_DATA', data_home / 'ps2-save-manager' / 'web')))
    args = parser.parse_args()
    try:
        from waitress import serve
    except ImportError:
        parser.error('Install the application first: python -m pip install .')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    service = Service(args.data_dir)
    service.start()
    logging.info('Save Manager: http://%s:%s · data: %s', args.host, args.port, service.data_dir)
    try:
        serve(Application(service, os.environ.get('SAVE_MANAGER_TOKEN', '')), host=args.host, port=args.port, threads=8)
    finally:
        service.close()


if __name__ == '__main__':
    main()
