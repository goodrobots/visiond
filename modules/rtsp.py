import gi
import logging

gi.require_version('GstRtspServer', '1.0')
from gi.repository import GstRtspServer

### Create an RTSP Media Factory from an existing pipeline
class MavRTSPMediaFactory(GstRtspServer.RTSPMediaFactory):
    def __init__(self, pipeline):
        self.logger = logging.getLogger('visiond.' + __name__)
        self.logger.info("Overriding RTSPMediaFactory with constructed pipeline")
        GstRtspServer.RTSPMediaFactory.__init__(self)
        self.set_shared(True)
        #self.set_reusable(True)
        self.set_eos_shutdown(False)
        self.set_buffer_size(0)
        #self.set_suspend_mode(GstRtspServer.RTSPMedia.GST_RTSP_SUSPEND_MODE_NONE)
        self.set_property('latency', 0)
        self.set_transport_mode(GstRtspServer.RTSPTransportMode.PLAY)
        self.pipeline = pipeline

    def do_create_element(self, url):
        self.logger.info("Creating RTSP factory element: {}".format(url))
        return self.pipeline
