"""python -m toolsvc   # serve the tools API (port TOOLSVC_PORT, default 8002)"""

import logging

from toolsvc.controllers.http_tools import create_app
from toolsvc.config import get_settings

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    create_app().run(host=get_settings().host, port=get_settings().port)
