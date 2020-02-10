import gi
import os
import signal
import subprocess

from .rtsp import *

gi.require_version('Gst', '1.0')
gi.require_version('GstRtspServer', '1.0')
from gi.repository import GLib, Gst, GstRtspServer
Gst.init(None)

### Streamer Class to build up Gstreamer pipeline from in to out
class Streamer(object):
    def __init__(self, config, logger, width, height, framerate, format, pixelformat, encoder, input, source, brightness, output, dest, port):
        self.size = 0
        self.playing = False
        self.paused = False
        self.format = format
        self.encoder = encoder
        self.payload = None # This is worked out later based on encoding and output type
        self.width = width
        self.height = height
        self.framerate = framerate
        self.output = output
        self.config = config
        self.logger = logger

        # Start with creating a pipeline from source element
        if input == "appsrc":
            self.input_appsrc()
        elif input == "v4l2":
            self.input_v4l2(source, brightness)
        
        # Next deal with each input format separately and interpret the stream and encoding method to the pipeline
        if format == "h264":
            self.capstring = 'video/x-h264,width='+str(width)+',height='+str(height)+',framerate='+str(framerate)+'/1'
            self.stream_h264()
        elif format == "mjpeg":
            self.capstring = 'image/jpeg,width='+str(width)+',height='+str(height)+',framerate='+str(framerate)+'/1'
            self.stream_mjpeg()
        elif format == "yuv":
            if pixelformat:
                self.capstring = 'video/x-raw,format='+pixelformat+',width='+str(width)+',height='+str(height)+',framerate='+str(framerate)+'/1'
            else:
                self.capstring = None
            self.stream_yuv()
        else:
            self.logger.handle.critical("Stream starting with unrecognised video format: " + str(format))
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
            self.logger.handle.critical("Stream starting with unrecognised encoder: " + str(encoder))
            return
        
        # Then work out which payload we want.  We only want a payload for unicast udp
        if output == "udp" or output == "rtsp":
            if encoder == "h264":
                self.payload = "rtp264pay"
                self.payload_h264()
            elif encoder == "mjpeg":
                self.payload = "rtpjpegpay"
                self.payload_mjpeg()
        else:
            self.payload_attach = self.encode_attach

        # Finally connect the requested output to the end of the pipeline
        if output == "file":
            self.output_file(dest)
        elif output == "udp":
            self.output_udp(dest, port)
        elif output == "dynudp":
            self.output_dynudp()
        elif output == "rtsp":
            self.output_rtsp(dest, port)
        elif output == "wcast":
            self.output_wcast(dest, port)

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
        self.logger.handle.info("Pipeline: \"" + " ! ".join(list(reversed(pipe_elements))) +"\"")
        
    ### Input methods
    def input_appsrc(self):
        self.logger.handle.info("Attaching input 'appsrc'")
        self.pipeline = Gst.Pipeline.new()
        self.source = Gst.ElementFactory.make("appsrc", "source")
        self.pipeline.add(self.source)
        # Set appsrc stream to live
        self.source.set_property("is-live",True)
        # Let appsrc set the timestamp so we don't have to do it
        self.source.set_property("do-timestamp",True)
        self.source.set_property("min-latency",0)
    
    def input_v4l2(self, source, brightness):
        if not source:
            source = "/dev/video0"
        self.logger.handle.info("Attaching input 'v4l2': "+str(source))
        self.pipeline = Gst.Pipeline.new()
        self.source = Gst.ElementFactory.make("v4l2src", "v4l2-source")
        self.source.set_property("device", source)
        self.source.set_property("brightness", brightness)
        self.pipeline.add(self.source)

    ### Stream methods
    def stream_h264(self):
        self.logger.handle.info("Attaching stream 'h264'")
        capsfilter = Gst.ElementFactory.make("capsfilter", "capsfilter")
        capsfilter.set_property('caps', Gst.Caps.from_string(self.capstring))
        self.pipeline.add(capsfilter)
        self.source.link(capsfilter)
        """
        # Try and construct a parse element.
        parse = Gst.ElementFactory.make("h264parse", "h264parse")
        if parse:
            logger.debug('h264parse element created')
            self.pipeline.add(parse)
            capsfilter.link(parse)
        h264pay = Gst.ElementFactory.make("rtph264pay", "h264-payload")
        h264pay.set_property("config-interval", 1)
        h264pay.set_property("pt", 96)
        h264pay.set_property("name", "pay0") # Set pay%d for rtsp stream pickup
        self.pipeline.add(h264pay)
        if parse:
            logger.debug('Attaching h264pay to h264parse')
            parse.link(h264pay)
        else:
            logger.debug('Attaching h264pay direct to capsfilter')
            capsfilter.link(h264pay)
        self.source_attach = h264pay
        """
        self.source_attach = capsfilter

    def stream_mjpeg(self):
        self.logger.handle.info("Attaching stream 'mjpeg'")
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
                self.logger.handle.info("Raspberry hardware decoder detected, using omxmjpegdec as mjpeg decoder")
                dec = Gst.ElementFactory.make("omxmjpegdec", "omxmjpegdec")
            elif Gst.ElementFactory.find("jpegdec"):
                dec = Gst.ElementFactory.make("jpegdec", "jpegdec")
            if not dec:
                self.logger.handle.critical("Error: No jpeg decoder found for mjpeg stream, exiting")
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
        self.logger.handle.info("Attaching stream 'yuv'")
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

    ### Encoding methods
    def encode_h264(self):
        self.logger.handle.info("Attaching encoding 'h264'")
        # Try and detect encoder
        self.h264enc = None
        # Detect Raspberry/OMX
        if Gst.ElementFactory.find("omxh264enc"):
            self.logger.handle.info("Raspberry hardware encoder detected, using omxh264enc as h264 encoder")
            self.h264enc = Gst.ElementFactory.make("omxh264enc", "raspberry-h264-encode")
            self.h264enc.set_property('control-rate', 'variable')
            self.h264enc.set_property('target-bitrate', 2000000)
        """
        # Detect Odroid/MFC
        # Note this is disabled as it is now triggered on Raspberry and possibly other platforms, with latest gstreamer
        try:
            odroid264encoder = subprocess.check_output("gst-inspect-1.0 video4linux2 | grep 'H.264 Encoder' |awk -F: {'print $1'} |sed 's/\s//g' |tr -d '\n'", shell=True)
            if odroid264encoder:
                logger.info("Odroid MFC hardware encoder detected, attempting to use "+odroid264encoder+" as h264 encoder")
                self.h264enc = Gst.ElementFactory.make(odroid264encoder, "odroid-h264-encode")
                h264struct = Gst.Structure.new_empty("v4lencoder-extracontrols")
                h264struct.encode = True
                h264struct.h264_level = 10
                h264struct.h264_profile = 4
                h264struct.frame_level_rate_control_enable = 1
                h264struct.frame_skip_enable = 1
                h264struct.video_bitrate = 2000000
                self.h264enc.set_property('extra-controls', h264struct)
        except subprocess.CalledProcessError as e:
            pass
        """
        # Detect Intel/VAAPI
        try:
            # Determine vaapi encoder exists, but suppress output
            with open(os.devnull, "w") as devnull:
                vaapi264encoder = subprocess.call(["gst-inspect-1.0", "vaapih264enc"], stdout=devnull, stderr=devnull)
            if not vaapi264encoder:
                self.logger.handle.info("VAAPI hardware encoder detected, attempting to use vaapi as h264 encoder")
                self.h264enc = Gst.ElementFactory.make("vaapih264enc", "vaapih264enc")
        except subprocess.CalledProcessError as e:
            pass
        # Otherwise use software encoder
        if not self.h264enc:
            self.logger.handle.info("No hardware encoder detected, using software x264 encoder")
            self.h264enc = Gst.ElementFactory.make("x264enc", "x264-encode")
            self.h264enc.set_property('speed-preset', 1)
            self.h264enc.set_property('tune', 0x00000004)
        self.pipeline.add(self.h264enc)
        self.source_attach.link(self.h264enc)
        # If using raspberry hardware encoder, specify caps explicitly otherwise it can get upset when using rtspserver
        if Gst.ElementFactory.find("omxh264enc"):
            x264capsfilter = Gst.ElementFactory.make("capsfilter", "x264capsfilter")
            x264capsfilter.set_property('caps', Gst.Caps.from_string("video/x-h264,profile=high,width={},height={},framerate={}/1".format(self.width,self.height,self.framerate)))
            self.pipeline.add(x264capsfilter)
            self.h264enc.link(x264capsfilter)
            self.encode_attach = x264capsfilter
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
        self.logger.handle.info("Attaching payload 'h264'")
        # Try and construct a parse element.  This will fail on later (1.9+) gstreamer versions, so the subsequent (if parse) code bypasses if not present
        parse = Gst.ElementFactory.make("h264parse", "h264parse")
        if parse:
            self.logger.handle.debug('h264parse element created')
            self.pipeline.add(parse)
            self.encode_attach.link(parse)
        h264pay = Gst.ElementFactory.make("rtph264pay", "h264-payload")
        h264pay.set_property("config-interval", 1)
        h264pay.set_property("pt", 96)
        h264pay.set_property("name", "pay0") # Set pay%d for rtsp stream pickup
        self.pipeline.add(h264pay)
        if parse:
            self.logger.handle.debug('Attaching h264pay to h264parse')               
            parse.link(h264pay)
        else:
            self.logger.handle.debug('Attaching h264pay direct to h264 encoder')
            self.encode_attach.link(h264pay)
        self.payload_attach = h264pay
            
    def payload_mjpeg(self):
        self.logger.handle.info("Attaching payload 'mjpeg'")
        mjpegpay = Gst.ElementFactory.make("rtpjpegpay", "mjpeg-payload")
        mjpegpay.set_property("pt", 26)
        self.pipeline.add(mjpegpay)
        self.encode_attach.link(mjpegpay)
        self.payload_attach = mjpegpay

    ### Output methods
    def output_file(self, dest):
        self.logger.handle.info("Attaching output 'file'")
        mux = Gst.ElementFactory.make("mpegtsmux", "mux")
        self.pipeline.add(mux)
        self.payload_attach.link(mux)
        
        sink = Gst.ElementFactory.make("filesink", "sink")
        sink.set_property("location", dest)
        self.pipeline.add(sink)
        mux.link(sink)
    
    def output_udp(self, dest, port):
        if not dest:
            self.logger.handle.warn("UDP destination must be set")
            return
        self.logger.handle.info("Attaching output 'udp', sending to "+str(dest)+":"+str(port))
        sink = Gst.ElementFactory.make("udpsink", "udpsink")
        sink.set_property("host", dest)
        sink.set_property("port", port)
        sink.set_property("sync", False)
        self.pipeline.add(sink)
        self.payload_attach.link(sink)
    
    def output_wcast(self, dest, port):
        self.logger.handle.info("Attaching output 'wcast'")
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
        self.wcast_tx = subprocess.Popen(['/srv/maverick/software/wifibroadcast/tx','-b 8', '-r 4', '-f 1024', dest], stdin=read)
        self.logger.handle.info("wcast tx pid:" + str(self.wcast_tx.pid))
        signal.signal(signal.SIGTERM, self.shutdown_tx)

    def output_dynudp(self):
        self.logger.handle.info("Attaching output 'dynudp'")
        sink = Gst.ElementFactory.make("dynudpsink", "dynudpsink")
        sink.set_property("sync", False)
        sink.set_property("bind-address", "0.0.0.0")
        sink.set_property("bind-port", 5554)
        self.pipeline.add(sink)
        self.encode_attach.link(sink)

    def output_rtsp(self, dest, port):
        self.logger.handle.info("Attaching output 'rtsp'")
        self.rtspserver = GstRtspServer.RTSPServer()
        self.rtspserver.set_address(dest)
        self.rtspserver.set_service(str(port))

        try:
            # Here we override RTSPMediaFactory to use the constructed object pipeline rather than the usual
            #  set_launch which parses a pipeline string.
            self.rtspfactory = MavRTSPMediaFactory(self.pipeline, self.logger)
        except Exception as e:
            self.logger.handle.critical("Error creating rstpfactory: "+repr(e))
        
        # Add the /video endpoint.  More/dynamic endpoints will be added in the future
        self.rtspmounts = self.rtspserver.get_mount_points()
        self.rtspmounts.add_factory('/video', self.rtspfactory)
        self.rtspserver.attach(None)

        self.logger.handle.info("RTSP stream running at rtsp://"+str(dest)+":"+str(port)+"/video")

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
        if self.output != "rtsp":
            self.pipeline.set_state(Gst.State.PLAYING)
            self.playing = True
        self.logger.handle.info('Starting camera stream')
        GLib.MainLoop().run()

    def write(self,s):
        gstbuff = Gst.Buffer.new_wrapped(s)
        self.source.emit("push-buffer",gstbuff)

    def stop(self):
        self.pipeline.set_state(Gst.State.READY)
        self.playing = False
        self.logger.handle.info('Stopping camera stream')

    def flush(self):
        self.stop()
        
    def shutdown_tx(self, signum, frame):
        os.kill(self.wcast_tx.pid, signal.SIGTERM)
        sys.exit()

