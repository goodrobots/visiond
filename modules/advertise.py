import threading
import logging
import socket
import queue
from zeroconf import IPVersion, ServiceInfo, Zeroconf

class StreamAdvert(threading.Thread):
    def __init__(self, config):
        threading.Thread.__init__(self)
        self.daemon = True
        self.config = config
        self.logger = logging.getLogger('visiond.' + __name__)

        # Attempt to redirect the default handler into our log files
        default_zeroconf_logger = logging.getLogger("zeroconf")
        default_zeroconf_logger.setLevel(logging.DEBUG) # TODO: Set based on config
        default_zeroconf_logger.propagate = True
        for handler in logging.getLogger("visiond").handlers:
            default_zeroconf_logger.addHandler(handler)
        
        self.zeroconf = None
        self._should_shutdown = threading.Event()
        self._q = queue.Queue()

        self.ip_version = IPVersion.V4Only  # IPVersion.All
        
        self.desc = {
            "foo": "barr"
        }
        self.service_info = build_service_info(self.desc)

    def build_service_info(self, desc={}):
        return ServiceInfo(
            # TODO: FIXME come up with useful values for the info below
            "_http._tcp.local.",
            "visiond._http._tcp.local.",
            addresses=[socket.inet_aton('replace_with_interface_from_options')],
            port='replace_with_port_from_options',
            properties=desc.copy(),
            server=f"{socket.getfqdn()}.",
        )

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

    def register_service(self):
        self.zeroconf.register_service(self.service_info)
    
    def update_service(self, desc_update):
        # it does not look like there is a nice way to update
        #  the properties field of a service.
        #  Make a new service with the same details,
        #  but update the properties.

        # Merge the dicts and apply the updates
        self.desc = {**self.desc, **desc_update}
        service_info = self.build_service_info(self.desc)
        self.zeroconf.update_service(service_info)
        self.service_info = service_info
    
    def unregister_service(self):
        self.zeroconf.unregister_service(self.service_info)

    def shutdown(self):
        self._should_shutdown.set()

    def update(self, **desc_update):
        self._q.put_nowait(desc_update)