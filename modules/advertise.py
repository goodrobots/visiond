import threading
import logging
import socket
import queue
from uuid import uuid5
from zeroconf import IPVersion, ServiceInfo, Zeroconf


class StreamAdvert(threading.Thread):
    def __init__(self, config):
        threading.Thread.__init__(self)
        self.daemon = True
        self.config = config
        self.logger = logging.getLogger("visiond." + __name__)

        # Attempt to redirect the default handler into our log files
        default_zeroconf_logger = logging.getLogger("zeroconf")
        default_zeroconf_logger.setLevel(logging.INFO)  # TODO: Set based on config
        default_zeroconf_logger.propagate = True
        for handler in logging.getLogger("visiond").handlers:
            default_zeroconf_logger.addHandler(handler)

        self.zeroconf = None
        self._should_shutdown = threading.Event()
        self._q = queue.Queue()

        self.ip_version = IPVersion.V4Only  # IPVersion.All

    def build_service_info(self, props, _type='visiond'):
        _subdesc = self.config.args.name if self.config.args.name else socket.gethostname()
        _rtspurl = f"rtsp://{socket.getfqdn()}:{self.config.args.output_port}/video"
        if _type == 'visiond':
            return ServiceInfo(
                "_rtsp._udp.local.",
                f"{_type} ({_subdesc}) ._rtsp._udp.local.",
                addresses=[socket.inet_aton(self.config.args.output_dest)],
                port=int(self.config.args.output_port),
                properties={
                    "port": self.config.args.output_port, 
                    "name": _subdesc, 
                    "service_type": "visiond",
                    "rtspUrl": _rtspurl,
                    "uuid": self.instance_uuid(_rtspurl),
                }
            )
        elif _type == 'webrtc':
            _wsEndpoint = f"wss://{socket.getfqdn()}:6796"
            return ServiceInfo(
                "_webrtc._udp.local.",
                f"visiond-webrtc ({_subdesc})._webrtc._udp.local.",
                addresses=[socket.inet_aton('0.0.0.0')],
                port=6796,
                properties={
                    "port": 6796, 
                    "name": _subdesc, 
                    "service_type": "webrtc", 
                    "wsEndpoint": _wsEndpoint,
                    "uuid": self.instance_uuid(_wsEndpoint),
                },
            )

    def instance_uuid(self, url):
        # Create a repeatable uuid based on unique url
        return str(uuid5(uuid.NAMESPACE_URL, url))

    def run(self):
        self.logger.info("Zeroconf advertisement thread is starting...")
        try:
            self.zeroconf = Zeroconf(ip_version=self.ip_version)
            self.register_service(self.service_info)
        except OSError as e:
            # the port was blocked
            self.logger.info.error(
                f"Unable to start zeroconf advertisement thread due to {e}"
            )
            self.clean_up()

        while not self._should_shutdown.is_set():
            try:
                # The following will block for at most [timeout] seconds
                desc_update = self._q.get(block=True, timeout=2)
            except queue.Empty:
                desc_update = None
            if desc_update:
                self.update_service(desc_update)

        # We only get here when shutdown has been called
        self.clean_up()

    def clean_up(self):
        self.logger.info("Zeroconf advertisement thread is stopping...")
        if self.zeroconf:
            self.zeroconf.unregister_all_services()
            self.zeroconf.close()
        self.logger.info("Zeroconf advertisement thread has stopped.")

    def register_service(self, service_info):
        self.zeroconf.register_service(service_info, cooperating_responders=True)

    def update_service(self, desc_update):
        # it does not look like there is a nice way to update
        #  the properties field of a service.
        #  Make a new service with the same details,
        #  but update the properties.

        # Merge the dicts and apply the updates
        self.service_info = self.build_service_info(desc_update)
        self.zeroconf.update_service(self.service_info)

    def unregister_service(self):
        self.zeroconf.unregister_service(self.service_info)

    def shutdown(self):
        self._should_shutdown.set()

    def update(self, desc_update):
        self._q.put_nowait(desc_update)
