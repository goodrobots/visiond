import gi
import logging

gi.require_version('Gst', '1.0')
gi.require_version('GstRtspServer', '1.0')
from gi.repository import Gst, GstRtspServer

### Create an RTSP Media Factory from an existing pipeline
class MavRTSPMediaFactory(GstRtspServer.RTSPMediaFactory):
    def __init__(self, pipeline):
        self.logger = logging.getLogger('visiond.' + __name__)
        self.logger.info("Overriding RTSPMediaFactory with constructed pipeline")
        self.pipeline = pipeline
        GstRtspServer.RTSPMediaFactory.__init__(self)

    def do_create_element(self, url):
        self.logger.info("Creating RTSP factory element: {}".format(url.abspath))
        return self.pipeline

    def do_configure(self, rtsp_media):
        self.logger.debug('Configuring RTSPMedia: {}'.format(rtsp_media))
        rtsp_media.set_reusable(True)
        rtsp_media.set_shared(True)
        rtsp_media.set_buffer_size(0)
        rtsp_media.set_latency(0)
        rtsp_media.prepare()
        rtsp_media.set_pipeline_state(Gst.State.PLAYING)
