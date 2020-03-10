import gi
import logging
import os
import signal
import subprocess
import threading
import sys

from .rtsp import *
from .webrtc import *
from .webrtc_signalserver import *

gi.require_version('Gst', '1.0')
gi.require_version('GstRtspServer', '1.0')
from gi.repository import GLib, Gst, GstRtspServer
Gst.init(None)

### Streamer Class to build up Gstreamer pipeline from in to out
class Streamer(object):
    def __init__(self, config, format, pixelformat, encoder, input, device):
        self.config = config
        self.size = 0
        self.playing = False
        self.paused = False
        self.format = format
        self.encoder = encoder
        self.payload = None # This is worked out later based on encoding and output type
        self.device = device
        self.width = int(self.config.args.width)
        self.height = int(self.config.args.height)
        self.framerate = int(self.config.args.framerate)
        self.output = self.config.args.output
        self.dest = self.config.args.output_dest
        self.port = int(self.config.args.output_port)
        self.brightness = int(self.config.args.brightness)
        self.bitrate = int(self.config.args.bitrate)
        self.webrtc = None
        self.webrtc_signal_server = None
        self.glib_mainloop = None
        self.glib_thread = None
        self.logger = logging.getLogger('visiond.' + __name__)

        # Start with creating a pipeline from source element
        if input == "appsrc":
            self.input_appsrc()
        elif input == "v4l2":
            self.input_v4l2()
        elif input == "nvarguscamerasrc":
            self.input_tegra()
        
        # Next deal with each input format separately and interpret the stream and encoding method to the pipeline
        if format == "h264":
            self.capstring = 'video/x-h264,width='+str(self.width)+',height='+str(self.height)+',framerate='+str(self.framerate)+'/1'
            self.stream_h264()
        elif format == "mjpeg":
            self.capstring = 'image/jpeg,width='+str(self.width)+',height='+str(self.height)+',framerate='+str(self.framerate)+'/1'
            self.stream_mjpeg()
        elif format == "yuv":
            if pixelformat:
                self.capstring = 'video/x-raw,format='+pixelformat+',width='+str(self.width)+',height='+str(self.height)+',framerate='+str(self.framerate)+'/1'
            else:
                self.capstring = None
            self.stream_yuv()
        elif format == "tegra":
            self.capstring = 'video/x-raw(memory:NVMM), format=NV12,width='+str(self.width)+',height='+str(self.height)+',framerate='+str(self.framerate)+'/1'
            self.stream_tegra()
        else:
            self.logger.critical("Stream starting with unrecognised video format: " + str(format))
            return
        
        # Next choose the encoder
        if encoder == format:
            self.encode_attach = self.source_attach
            pass
        elif encoder == "h264":
            self.encode_h264()
        elif encoder == "mjpeg":
            self.encode_mjpeg()
        elif encoder == "yuv":
            self.encode_yuv()
        else:
            self.logger.critical("Stream starting with unrecognised encoder: " + str(encoder))
            return
        
        # Then work out which payload we want.
        if self.output == "udp" or self.output == "rtsp" or self.output == "webrtc":
            # For now, we fix webrtc to h264, which automatically invokes the rtp264pay that we also want
            if self.output == "webrtc":
                encoder = "h264"
            # Now set payloads according to the encoder
            if encoder == "h264":
                self.payload = "rtp264pay"
                self.payload_h264()
            elif encoder == "mjpeg":
                self.payload = "rtpjpegpay"
                self.payload_mjpeg()
        else:
            self.payload_attach = self.encode_attach

        # Finally connect the requested output to the end of the pipeline
        if self.output == "file":
            self.output_file()
        elif self.output == "udp":
            self.output_udp()
        elif self.output == "dynudp":
            self.output_dynudp()
        elif self.output == "rtsp":
            self.output_rtsp()
        elif self.output == "wcast":
            self.output_wcast()
        elif self.output == "webrtc":
            self.output_webrtc()

        # Start the pipeline
        self.show_pipeline()
        self.bus()
        self.start()

    def show_pipeline(self):        
        # Output the resulting pipeline construction to log
        pipeline_iterator = Gst.Bin.iterate_elements(self.pipeline)
        pipe_elements = []
        while True:
            res = Gst.Iterator.next(pipeline_iterator)
            if res[1]:
                elemstr = res[1].name
                # Extract caps if capsfilter element
                if res[1].name == "capsfilter":
                    elemstr += " '" + Gst.Caps.to_string(res[1].get_property('caps')) + "'"
                # Extract device if v4l2src element
                if res[1].name == "v4l2-source":
                    elemstr += " " + res[1].get_property('device')
                # Add element to pipeline output
                pipe_elements.append(elemstr)
                # logger.debug("Element: "+str(res[1]))
            if res[0] == Gst.IteratorResult.DONE:
                break
        self.logger.info("Pipeline: \"" + " ! ".join(list(reversed(pipe_elements))) +"\"")
        
    ### Input methods
    def input_appsrc(self):
        self.logger.info("Attaching input 'appsrc'")
        self.pipeline = Gst.Pipeline.new()
        self.source = Gst.ElementFactory.make("appsrc", "source")
        self.pipeline.add(self.source)
        # Set appsrc stream to live
        self.source.set_property("is-live",True)
        # Let appsrc set the timestamp so we don't have to do it
        self.source.set_property("do-timestamp",True)
        self.source.set_property("min-latency",0)
    
    def input_v4l2(self):
        if not self.device:
            self.device = "/dev/video0"
        self.logger.info("Attaching input 'v4l2': "+str(self.device))
        self.pipeline = Gst.Pipeline.new()
        self.source = Gst.ElementFactory.make("v4l2src", "v4l2-source")
        self.source.set_property("device", self.device)
        self.source.set_property("brightness", self.brightness)
        self.pipeline.add(self.source)

    def input_tegra(self):
        self.logger.info("Attaching input 'tegra' using nvarguscamerasrc")
        self.pipeline = Gst.Pipeline.new()
        self.source = Gst.ElementFactory.make("nvarguscamerasrc", "nvarguscamerasrc-source")
        self.pipeline.add(self.source)

    ### Stream methods
    def stream_h264(self):
        self.logger.info("Attaching stream 'h264'")
        capsfilter = Gst.ElementFactory.make("capsfilter", "capsfilter")
        capsfilter.set_property('caps', Gst.Caps.from_string(self.capstring))
        self.pipeline.add(capsfilter)
        self.source.link(capsfilter)
        self.source_attach = capsfilter

    def stream_mjpeg(self):
        self.logger.info("Attaching stream 'mjpeg'")
        capsfilter = Gst.ElementFactory.make("capsfilter", "capsfilter")
        capsfilter.set_property('caps', Gst.Caps.from_string(self.capstring))
        self.pipeline.add(capsfilter)
        self.source.link(capsfilter)
        # Try and construct a parse element.
        parse = Gst.ElementFactory.make("jpegparse", "jpegparse")
        if parse:
            self.pipeline.add(parse)
            capsfilter.link(parse)
        queue = Gst.ElementFactory.make("queue", "queue")
        if self.format != self.encoder:
            dec = None
            # if Gst.ElementFactory.find("omxmjpegdec"):
            if Gst.ElementFactory.find("omxmjpegdecDISABLED"):
                self.logger.info("Raspberry hardware decoder detected, using omxmjpegdec as mjpeg decoder")
                dec = Gst.ElementFactory.make("omxmjpegdec", "omxmjpegdec")
            elif Gst.ElementFactory.find("jpegdec"):
                dec = Gst.ElementFactory.make("jpegdec", "jpegdec")
            if not dec:
                self.logger.critical("Error: No jpeg decoder found for mjpeg stream, exiting")
                sys.exit(1)
            self.pipeline.add(dec)
            if parse:
                parse.link(dec)
            else:
                capsfilter.link(dec)
            self.pipeline.add(queue)
            dec.link(queue)
        else:
            self.pipeline.add(queue)
            if parse:
                parse.link(queue)
            else:
                capsfilter.link(queue)
        vconvert = Gst.ElementFactory.make("videoconvert", "videoconvert")
        self.pipeline.add(vconvert)
        queue.link(vconvert)
        self.source_attach = vconvert
        
    def stream_yuv(self):
        self.logger.info("Attaching stream 'yuv'")
        if self.capstring:
            capsfilter = Gst.ElementFactory.make("capsfilter", "capsfilter")
            capsfilter.set_property('caps', Gst.Caps.from_string(self.capstring))
            self.pipeline.add(capsfilter)
            self.source.link(capsfilter)
        queue = Gst.ElementFactory.make("queue", "queue")
        self.pipeline.add(queue)
        if self.capstring:
            capsfilter.link(queue)
        else:
            self.source.link(queue)
        vconvert = Gst.ElementFactory.make("autovideoconvert", "autovideoconvert")
        if not vconvert:
            vconvert = Gst.ElementFactory.make("videoconvert", "videoconvert")
        self.pipeline.add(vconvert)
        queue.link(vconvert)
        self.source_attach = vconvert

    def stream_tegra(self):
        self.logger.info("Attaching stream 'tegra'")
        capsfilter = Gst.ElementFactory.make("capsfilter", "capsfilter")
        capsfilter.set_property('caps', Gst.Caps.from_string(self.capstring))
        self.pipeline.add(capsfilter)
        self.source.link(capsfilter)
        self.source_attach = capsfilter
        
    ### Encoding methods
    def encode_h264(self, encoder_type = None):
        self.logger.info("Attaching encoding 'h264'")
        ### First attempt to detect the best encoder type for the platform
        _encoder_type = None
        # If encoder type is manually set, use it as an override
        if encoder_type:
            _encoder_type = encoder_type
        # Detect Nvidia encoder - note tegra hardware usually also has omx available so we detect this first
        elif Gst.ElementFactory.find("nvv4l2h264enc"):
            _encoder_type = "nvv4l2h264enc"
        # Detect OMX hardware
        elif Gst.ElementFactory.find("omxh264enc"):
            _encoder_type = "omxh264enc"
        # Detect Intel/VAAPI
        elif Gst.ElementFactory.find("vaapih264enc"):
            _encoder_type = "vaapih264enc"
        # If h264 encoding hardware was not detected, use software encoder
        else:
            _encoder_type = "x264"

        ### Create encoder element
        self.h264enc = None

        # Nvidia hardware
        if _encoder_type == "nvv4l2h264enc":
            self.logger.info("Nvidia hardware encoder detected, using nvv4l2h264enc as h264 encoder")
            self.h264enc = Gst.ElementFactory.make("nvv4l2h264enc", "nvidia-h264-encode")
            self.h264enc.set_property('control-rate', 0) # 0=variable, 1=constant
            self.h264enc.set_property('bitrate', self.bitrate)
            self.h264enc.set_property('maxperf-enable', 1)
            self.h264enc.set_property('preset-level', 1) # 1 = UltraFast
            self.h264enc.set_property('MeasureEncoderLatency', 1)
            self.h264enc.set_property('profile', 0) # 0 = BaseProfile which should usually be set for max compatibility particularly with webrtc. 2 = Main, 4 = High

        # OMX hardware
        elif _encoder_type == "omxh264enc":
            self.logger.info("OMX hardware encoder detected, using omxh264enc as h264 encoder")
            self.h264enc = Gst.ElementFactory.make("omxh264enc", "omx-h264-encode")
            self.h264enc.set_property('control-rate', 3) # 1: variable, 2: constant, 3: variable-skip-frames, 4: constant-skip-frames
            self.h264enc.set_property('target-bitrate', self.bitrate)

        # Intel hardware
        elif _encoder_type == "vaapih264enc":
            self.logger.info("VAAPI hardware encoder detected, using vaapih264enc as h264 encoder")
            self.h264enc = Gst.ElementFactory.make("vaapih264enc", "vaapi-h264-encode")
            self.h264enc.set_property('control-rate', 0) # 0=variable, 1=constant
            self.h264enc.set_property('target-bitrate', self.bitrate)
            
        # Software encoder
        elif _encoder_type == "x264":
            self.logger.info("No hardware encoder detected, using software x264 encoder")
            self.h264enc = Gst.ElementFactory.make("x264enc", "x264-encode")
            self.h264enc.set_property('speed-preset', 1)
            self.h264enc.set_property('tune', 0x00000004)
            self.h264enc.set_property('bitrate', self.bitrate / 1024)

        # Attach the h264 element
        self.pipeline.add(self.h264enc)
        self.source_attach.link(self.h264enc)

        # If using omx hardware encoder, specify caps explicitly otherwise it can get upset when using rtspserver
        if _encoder_type == "omxh264enc":
            h264capsfilter = Gst.ElementFactory.make("capsfilter", "h264capsfilter")
            h264capsfilter.set_property('caps', Gst.Caps.from_string("video/x-h264,profile=high,width={},height={},framerate={}/1".format(self.width,self.height,self.framerate)))
            self.pipeline.add(h264capsfilter)
            self.h264enc.link(h264capsfilter)
            self.encode_attach = h264capsfilter
        else:
            self.encode_attach = self.h264enc
        
    def encode_mjpeg(self):
        # TODO: Add actual mjpeg encoding, currently we just pass through the source to the encoder attach points
        if self.format == self.encoder:
            self.encode_attach = self.source_attach
        else:
            self.encode_attach = self.source_attach
        
    def encode_yuv(self):
        # Nothing todo, just hang the source onto the encoder attach point
        self.encode_attach = self.source_attach

    ### Payload methods
    def payload_h264(self):
        self.logger.info("Attaching payload 'h264'")
        # Attach an h264parse element.
        parse = Gst.ElementFactory.make("h264parse", "h264parse")
        if parse:
            self.logger.debug('h264parse element created')
            self.pipeline.add(parse)
            self.encode_attach.link(parse)
        h264pay = Gst.ElementFactory.make("rtph264pay", "h264-payload")
        h264pay.set_property("config-interval", 1)
        h264pay.set_property("pt", 96)
        h264pay.set_property("name", "pay0") # Set pay%d for rtsp stream pickup
        self.pipeline.add(h264pay)
        if parse:
            self.logger.debug('Attaching h264pay to h264parse')               
            parse.link(h264pay)
        else:
            self.logger.debug('Attaching h264pay direct to h264 encoder')
            self.encode_attach.link(h264pay)
        self.payload_attach = h264pay
            
    def payload_mjpeg(self):
        self.logger.info("Attaching payload 'mjpeg'")
        mjpegpay = Gst.ElementFactory.make("rtpjpegpay", "mjpeg-payload")
        mjpegpay.set_property("pt", 26)
        self.pipeline.add(mjpegpay)
        self.encode_attach.link(mjpegpay)
        self.payload_attach = mjpegpay

    ### Output methods
    def output_file(self):
        self.logger.info("Attaching output 'file'")
        mux = Gst.ElementFactory.make("mpegtsmux", "mux")
        self.pipeline.add(mux)
        self.payload_attach.link(mux)
        
        sink = Gst.ElementFactory.make("filesink", "sink")
        sink.set_property("location", self.dest)
        self.pipeline.add(sink)
        mux.link(sink)
    
    def output_udp(self):
        if not self.dest:
            self.logger.warn("UDP destination must be set")
            return
        self.logger.info("Attaching output 'udp', sending to "+str(self.dest)+":"+str(self.port))
        sink = Gst.ElementFactory.make("udpsink", "udpsink")
        sink.set_property("host", self.dest)
        sink.set_property("port", self.port)
        sink.set_property("sync", False)
        self.pipeline.add(sink)
        self.payload_attach.link(sink)
    
    def output_wcast(self):
        self.logger.info("Attaching output 'wcast'")
        # Create an OS pipe so we can attach the gstream pipeline to one end, and wifibroadcast tx to the other end
        read, write = os.pipe()
        # Create an fdsink to dump the pipeline out of
        sink = Gst.ElementFactory.make("fdsink", "fdsink")
        # Attach the sink to one end of the os pipe
        sink.set_property("fd", write)
        sink.set_property("sync", False)
        self.pipeline.add(sink)
        self.payload_attach.link(sink)
        # Spawn wifibroadcast tx
        self.wcast_tx = subprocess.Popen(['/srv/maverick/software/wifibroadcast/tx','-b 8', '-r 4', '-f 1024',self.dest], stdin=read)
        self.logger.info("wcast tx pid:" + str(self.wcast_tx.pid))
        signal.signal(signal.SIGTERM, self.shutdown_tx)

    def output_dynudp(self):
        self.logger.info("Attaching output 'dynudp'")
        sink = Gst.ElementFactory.make("dynudpsink", "dynudpsink")
        sink.set_property("sync", False)
        sink.set_property("bind-address", "0.0.0.0")
        sink.set_property("bind-port", self.port)
        self.pipeline.add(sink)
        self.encode_attach.link(sink)

    def output_rtsp(self):
        self.logger.info("Attaching output 'rtsp'")
        self.rtspserver = GstRtspServer.RTSPServer()
        self.rtspserver.set_address(self.dest)
        self.rtspserver.set_service(str(self.port))

        try:
            # Here we override RTSPMediaFactory to use the constructed object pipeline rather than the usual
            #  set_launch which parses a pipeline string.
            self.rtspfactory = MavRTSPMediaFactory(self.pipeline)
        except Exception as e:
            self.logger.critical("Error creating rstpfactory: "+repr(e))
        
        # Add the /video endpoint.  More/dynamic endpoints will be added in the future
        self.rtspmounts = self.rtspserver.get_mount_points()
        self.rtspmounts.add_factory('/video', self.rtspfactory)
        self.rtspserver.attach(None)

        self.logger.info("RTSP stream running at rtsp://"+str(self.dest)+":"+str(self.port)+"/video")

    def output_webrtc(self):
        self.logger.info("Creating WebRTC Signal Server")
        self.webrtc_signal_server = MavWebRTCSignalServer(self.config)
        self.logger.info("Attaching output 'webrtc'")
        sink = Gst.ElementFactory.make("webrtcbin", "webrtc")
        self.pipeline.add(sink)
        self.payload_attach.link(sink)
        self.our_webrtcid = 12345
        self.webrtc = MavWebRTC(self.pipeline, self.our_webrtcid, self.config)
        self.webrtc.start()

    ### Misc methods (glib introspection)
    def on_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            self.pipeline.set_state(Gst.State.NULL)
            self.playing = False
        elif t == Gst.MessageType.ERROR:
            self.pipeline.set_state(Gst.State.NULL)
            err, debug = message.parse_error()
            print("Error: %s" % err, debug)
            self.playing = False

    def bus(self):
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_message)

    ### Action methods
    def start(self):
        # TODO: check to make sure we are not already playing...
        if self.output not in ["rtsp", "webrtc"]:
            self.pipeline.set_state(Gst.State.PLAYING)
            self.playing = True
        self.logger.info('Starting camera stream')
        self.glib_mainloop = GLib.MainLoop()
        # self.glib_mainloop.run() is a blocking call
        #  Wrap the call in a thread:
        self.glib_thread = threading.Thread(target=self.glib_mainloop.run, daemon=True)
        self.glib_thread.start()

        # or let it block here...
        # self.glib_mainloop.run()

    def write(self,s):
        gstbuff = Gst.Buffer.new_wrapped(s)
        self.source.emit("push-buffer",gstbuff)

    def stop(self):
        self.logger.info('Stopping camera stream')
        if self.glib_thread and self.glib_thread.is_alive() and self.glib_mainloop.is_running():
        # if self.glib_mainloop.is_running():    
            self.glib_mainloop.quit()
            self.glib_thread.join() # wait for the thread to finish
        self.playing = False
        self.pipeline.set_state(Gst.State.READY)

    def flush(self):
        self.stop()
        
    def shutdown_tx(self, signum, frame):
        os.kill(self.wcast_tx.pid, signal.SIGTERM)
        sys.exit()

