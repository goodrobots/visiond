import threading
import logging
import tornado.ioloop
import tornado.web
import tornado.websocket
from tornado.options import define, options

define("port", default=1235, help="Port to listen on", type=int)


class TApp(tornado.web.Application):
    def __init__(self):
        # Setup websocket handler
        handlers = [(r"/", JanusHandler)]
        settings = dict(
            cookie_secret="asdlkfjhfiguhefgrkjbfdlgkjadfh", xsrf_cookies=True,
        )
        super(TApp, self).__init__(handlers, **settings)


class JanusHandler(tornado.websocket.WebSocketHandler):
    def open(self):
        self.logger = logging.getLogger("visiond.janushandler")

    def on_close(self):
        self.logger.info("Closing JanusHandler websocket connection")

    def on_message(self, message):
        parsed = tornado.escape.json_decode(message)
        self.logger.debug("got message %r", message)

    def get_compression_options(self):
        return {}


class JanusInterface(threading.Thread):
    def __init__(self, config):
        threading.Thread.__init__(self)
        self.daemon = True
        self.config = config
        self.logger = logging.getLogger("visiond." + __name__)

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
        self.loop = tornado.ioloop.IOLoop.current()

    def run(self):
        self.logger.info("Janus interface thread is starting...")

        application = TApp()
        self.server = tornado.httpserver.HTTPServer(application)
        self.server.listen(port=options.port)

        self.loop.start()
        # this function blocks at this point until the server
        #  is asked to exit via shutdown()
        self.logger.info("Janus interface thread has stopped.")

    def shutdown(self):
        self.loop.add_callback(self.loop.stop)
        self.logger.info("Janus interface thread is stopping...")
