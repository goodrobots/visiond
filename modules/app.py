import errno
import gi
import glob
import io
import logging
import os
import re
import time
import v4l2
import signal
import sys
import traceback
from fcntl import ioctl

from .config import *
from .streamer import *
from .advertise import StreamAdvert
from .janus import JanusInterface

gi.require_version('Gst', '1.0')
from gi.repository import GLib,Gst
Gst.init(None)

### Main visiond App Class
class visiondApp():
    def __init__(self, config):
        self.config = config
        self.logger = logging.getLogger('visiond.' + __name__)
        self.stream = None
        self.zeroconf = None
        self.janus = None
        self._should_shutdown = False

        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, sig, frame):
        self.shutdown()

    def run(self):
        self.logger.info("Starting maverick-visiond")

        if 'debug' in self.config.args and self.config.args.debug:
            Gst.debug_set_active(True)
            Gst.debug_set_default_threshold(self.config.args.debug)
        
        if 'retry' not in self.config.args or not self.config.args.retry:
            self.retry = 30
        else:
            self.retry = float(self.config.args.retry)

        # Start the zeroconf thread
        if self.config.args.zeroconf:
            self.zeroconf = StreamAdvert(self.config)
            self.zeroconf.start()
        else:
            self.zeroconf = None

        self.janus = JanusInterface(self.config, self.zeroconf)
        self.janus.start()

        # Start the pipeline.  Trap any errors and wait for 30sec before trying again.
        while not self._should_shutdown:
            try:
                if 'pipeline_override' in self.config.args and self.config.args.pipeline_override:
                    self.logger.info("pipeline_override set, constructing manual pipeline")
                    self.manualconstruct()
                else:
                    self.logger.info("pipeline_override is not set, auto-constructing pipeline")
                    self.autoconstruct()
            except ValueError as e:
                self.logger.critical("Error constructing pipeline: {}, retrying in {} sec".format(repr(e), self.retry))
                self.logger.critical(traceback.print_exc())
                time.sleep(self.retry)

    def manualconstruct(self):
        if self.config.args.pipeline_override not in self.config.args:
            self.logger.critical('manualconstruct() called but no pipeline_override config argument specified')
            sys.exit(1)
        self.logger.info("Manual Pipeline Construction")
        self.logger.info("Creating pipeline from config: " + self.config.args.pipeline_override)
        try:
            # Create the pipeline from config override
            self.pipeline = Gst.parse_launch(self.config.args.pipeline_override)
            # Set pipeline to playing
            self.pipeline.set_state(Gst.State.PLAYING)
        except Exception as e:
            raise ValueError('Error constructing manual pipeline specified: {}'.format(repr(e)))
        while True:
            time.sleep(5)

    def autoconstruct(self):
        # If camera device set in config use it, otherwise autodetect
        cameradev = None
        devicepaths = glob.glob("/dev/video*")
        if self.config.args.camera_device:
            self.logger.debug('camera_device specified: {}'.format(self.config.args.camera_device))
            cameradev = self.config.args.camera_device
        else:
            # device not set, carry on and try to autodetect
            for devicepath in sorted(devicepaths):
                if not cameradev and self.check_input(devicepath):
                    cameradev = devicepath
                    self.logger.info('v4l2 device '+devicepath+' is a camera, autoselecting')
                elif not cameradev:
                    self.logger.debug('v4l2 device '+devicepath+' is not a camera, ignoring')
        if not cameradev:
            raise ValueError('Error detecting camera video device')

        # Check the camera has a valid input
        try:
            self.vd = io.TextIOWrapper(open(cameradev, "r+b", buffering=0))
            cp = v4l2.v4l2_capability()
        except Exception as e:
            raise ValueError("Camera not specified in config, or camera not valid: {}".format(repr(e)))
        if not self.check_input():
            raise ValueError('Specified camera not valid')

        # Log info
        self.camera_info()
        
        # Try and autodetect Jetson/Tegra CSI connection
        if self.driver == 'tegra-video' and ('input' not in self.config.args or not self.config.args.input):
            self.logger.info('Nvidia Jetson/Tegra CSI connection detected, switching to nvarguscamerasrc')
            self.input = "nvarguscamerasrc"
        elif 'input' not in self.config.args or not self.config.args.input:
            self.input = "v4l2src"
        else:
            self.input = self.config.args.input

        # Try and autodetect MFC device
        self.mfcdev = None
        for devicepath in devicepaths:
            dp = io.TextIOWrapper(open(devicepath, "r+b", buffering=0))
            ioctl(dp, v4l2.VIDIOC_QUERYCAP, cp)
            if cp.card == "s5p-mfc-enc":
                self.mfcdev = dp
                self.logger.info(f'MFC Hardware encoder detected, autoselecting {devicepath}')

        # If format set in config use it, otherwise autodetect
        streamtype = None
        if self.config.args.format:
            streamtype = self.config.args.format
        else:
            if self.input == "nvarguscamerasrc":
                self.logger.info('Nvidia Jetson/Tegra input detected, forcing Tegra stream format')
                streamtype = 'tegra'
            elif re.search("C920", self.card):
                self.logger.info("Logitech C920 detected, forcing H264 passthrough")
                streamtype = 'h264'                                                                     
            # format not set, carry on and try to autodetect
            elif self.check_format('yuv'):
                self.logger.info('Camera YUV stream available, using yuv stream')
                streamtype = 'yuv'
            # Otherwise, check for an mjpeg->h264 encoder pipeline.
            elif self.check_format('mjpeg'):
                self.logger.info('Camera MJPEG stream available, using mjpeg stream')
                streamtype = 'mjpeg'
            # Lastly look for a h264 stream
            elif self.check_format('h264'):
                self.logger.info('Camera H264 stream available, using H264 stream')
                streamtype = 'h264'
        if not streamtype:
            raise ValueError('Error detecting camera video format')

        # If encoder set in config use it, otherwise set to h264
        encoder = None
        if self.config.args.encoder:
            encoder = self.config.args.encoder
        if not encoder:
            encoder = "h264"
        self.logger.debug("Using encoder: {}".format(encoder))
        
        # If raspberry camera detected set pixelformat to I420, otherwise set to YUY2 by default
        pixelformat = "YUY2"
        ioctl(self.vd, v4l2.VIDIOC_QUERYCAP, cp) 
        if cp.driver == "bm2835 mmal":
            self.logger.info("Raspberry Pi Camera detected, setting pixel format to I420")
            pixelformat = "I420"
            
        # If raw pixelformat set in config override the defaults
        if 'pixelformat' in self.config.args and self.config.args.pixelformat:
                pixelformat = self.config.args.pixelformat
        self.logger.debug("Using pixelformat: {}".format(pixelformat))

        # Create and start the stream
        try:
            self.logger.info("Creating stream object - device: {}, stream: {}, pixelformat: {}, encoder: {}, input: {}".format(cameradev, streamtype, pixelformat, encoder, self.input))
            Streamer(self.config, streamtype, pixelformat, encoder, self.input, cameradev)
            if self.zeroconf:
                # Update the stream advertisement with the new info
                self.zeroconf.update({"stream":"replace_with_stream_info"})
        except Exception as e:
            if self.zeroconf:
                self.zeroconf.update({"stream":""})
            raise ValueError('Error creating {} stream: {}'.format(streamtype, repr(e)))

        while not self._should_shutdown:
            time.sleep(1)

    def camera_info(self):
        # Log capability info
        cp = v4l2.v4l2_capability() 
        ioctl(self.vd, v4l2.VIDIOC_QUERYCAP, cp) 
        self.logger.debug("driver: " + cp.driver.decode())
        self.logger.debug("card: " + cp.card.decode())
        self.driver = cp.driver.decode()
        self.card = cp.card.decode()
        
        # Log controls available
        queryctrl = v4l2.v4l2_queryctrl(v4l2.V4L2_CID_BASE)
        while queryctrl.id < v4l2.V4L2_CID_LASTP1:
            try:
                ioctl(self.vd, v4l2.VIDIOC_QUERYCTRL, queryctrl)
            except IOError as e:
                # this predefined control is not supported by this device
                assert e.errno == errno.EINVAL
                queryctrl.id += 1
                continue
            self.logger.debug("Camera control: " + queryctrl.name.decode())
            queryctrl = v4l2.v4l2_queryctrl(queryctrl.id + 1)
        queryctrl.id = v4l2.V4L2_CID_PRIVATE_BASE
        while True:
            try:
                ioctl(self.vd, v4l2.VIDIOC_QUERYCTRL, queryctrl)
            except IOError as e:
                # no more custom controls available on this device
                assert e.errno == errno.EINVAL
                break
            self.logger.debug("Camera control: " + queryctrl.name.decode())
            queryctrl = v4l2.v4l2_queryctrl(queryctrl.id + 1)
        
        # Log formats available
        capture = v4l2.v4l2_fmtdesc()
        capture.index = 0
        capture.type = v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE
        try:
            while (ioctl(self.vd, v4l2.VIDIOC_ENUM_FMT, capture) >= 0):
                    self.logger.debug("Camera format: " + capture.description.decode())
                    capture.index += 1
        except:
            pass
        
    def check_input(self, vd=None, index=0):
        if vd == None:
            vd = self.vd
        else:
            vd = io.TextIOWrapper(open(vd, "r+b", buffering=0))
        input = v4l2.v4l2_input(index)
        try:
            ioctl(vd, v4l2.VIDIOC_ENUMINPUT, input)
            self.logger.debug('V4l2 device input: ' + input.name.decode() + ':' + str(input.type))
            if input.type != 2:
                return False # If input type is not camera (2) then return false
            return True
        except Exception as e:
            self.logger.debug("Error checking input: {}".format(repr(e)))
            return False

    def check_format(self, format):
        capture = v4l2.v4l2_fmtdesc()
        capture.index = 0
        capture.type = v4l2.V4L2_BUF_TYPE_VIDEO_CAPTURE
        available = False
        try:
            while (ioctl(self.vd, v4l2.VIDIOC_ENUM_FMT, capture) >= 0):
                self.logger.debug("Checking format: {} : {}".format(format, capture.description.decode()))
                if format.lower() == "h264":
                    if re.search('H264', capture.description.decode().lower()) or re.search('H.264', capture.description.decode().lower()):
                        available = True
                elif format.lower() == "mjpeg":
                    if re.search('jpeg', capture.description.decode().lower()):
                        available = True
                elif format.lower() == "yuv" or format.lower() == "raw":
                    if re.search('^yu', capture.description.decode().lower()):
                        available = True
                else:
                    if re.search(format.lower(), capture.description.decode().lower()):
                        available = True
                capture.index += 1
        except:
            pass
        return available

    def shutdown(self):
        self._should_shutdown = True
        self.logger.info("Shutting down visiond")
        if self.stream:
            if self.stream.webrtc:
                self.stream.webrtc.shutdown()
            if self.stream.webrtc_signal_server:
                self.stream.webrtc_signal_server.shutdown()
                self.stream.webrtc_signal_server.join()
            self.stream.stop()
        if self.janus:
            self.janus.shutdown()
            self.janus.join()
        if self.zeroconf:
            self.zeroconf.shutdown()
            self.zeroconf.join()
