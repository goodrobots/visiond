import threading
import logging
import asyncio
import requests
from urllib3.exceptions import InsecureRequestWarning
import socket
import tornado.ioloop
import tornado.web
import tornado.websocket
from tornado.options import define, options
from zeroconf import IPVersion, ServiceInfo, Zeroconf

define("port", default=1235, help="Port to listen on", type=int)
define(
    "interface",
    default="127.0.0.1",
    type=str,
    help="Interface to listen on: 0.0.0.0 represents all interfaces",
)


class TApp(tornado.web.Application):
    def __init__(self, zeroconf, config):
        # Setup websocket handler
        handlers = [(r"/", JanusHandler, {'zeroconf': zeroconf, 'config': config})]
        settings = dict(
            cookie_secret="asdlkfjhfiguhefgrkjbfdlgkjadfh", xsrf_cookies=True,
        )
        super(TApp, self).__init__(handlers, **settings)


class JanusHandler(tornado.websocket.WebSocketHandler):
    def initialize(self, zeroconf, config):
        self.zeroconf = zeroconf
        self.config = config
        
    def open(self):
        self.logger = logging.getLogger("visiond.janushandler")
        self.logger.info("Opening JanusHandler websocket connection")

    def on_close(self):
        self.logger.info("Closing JanusHandler websocket connection")

    def on_message(self, message):
        parsed = tornado.escape.json_decode(message)
        self.logger.debug("got message %r", message)
        if parsed['type'] == 256:
            _serviceinfo = self.zeroconf.build_service_info({}, _type='webrtc')
            self.zeroconf.register_service(_serviceinfo)
        
    def get_compression_options(self):
        return {}

    def check_origin(self, origin):
        return True

class JanusInterface(threading.Thread):
    def __init__(self, config, zeroconf):
        threading.Thread.__init__(self)
        self.daemon = True
        self.config = config
        self.zeroconf = zeroconf
        self.logger = logging.getLogger("visiond." + __name__)

        self.get_info()

        # Attempt to redirect the default handlers into our log files
        tornado_loggers = [
            "tornado.websocket",
            "tornado.application",
            "tornado.general",
            "tornado.access",
        ]
        for tornado_logger in tornado_loggers:
            default_tornado_logger = logging.getLogger(tornado_logger)
            default_tornado_logger.setLevel(logging.DEBUG)  # TODO: Set based on config
            default_tornado_logger.propagate = True
            for handler in logging.getLogger("visiond").handlers:
                default_tornado_logger.addHandler(handler)

        self._should_shutdown = threading.Event()

    def get_info(self):
        try:
            url = 'https://localhost:6795/janus/info'
            requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
            _request = requests.get(url='https://localhost:6795/janus/info', verify=False)
            _data = _request.json()
            if _data['janus'] == 'server_info' and _data['server-name'] == 'Maverick':
                self.logger.info("Maverick janus webrtc service detected, registering with zeroconf")
                _serviceinfo = self.zeroconf.build_service_info({}, _type='webrtc')
                self.zeroconf.register_service(_serviceinfo)
        except Exception as e:
            self.logger.info("Maverick janus webrtc service not detected, skipping zeroconf registration")

    def run(self):
        self.logger.info("Janus interface thread is starting...")
        asyncio.set_event_loop(asyncio.new_event_loop())
        self.ioloop = tornado.ioloop.IOLoop.current()
        tornado.ioloop.PeriodicCallback(self.check_for_shutdown, 1000, jitter = 0.1).start()
        application = TApp(self.zeroconf, self.config)
        server = tornado.httpserver.HTTPServer(application, ssl_options=None)
        server.listen(port=options.port, address=options.interface)
        self.ioloop.start()
        # this function blocks at this point until the server
        #  is asked to exit via shutdown()
        self.logger.info("Janus interface thread has stopped.")

    def check_for_shutdown(self):
        if self._should_shutdown.is_set():
            self.ioloop.add_callback(self.ioloop.stop)
            self.logger.info("Janus interface thread is stopping...")

    def shutdown(self):
        self._should_shutdown.set()
