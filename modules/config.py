# -*- coding: utf-8 -*- 

from __future__ import print_function

import sys
import argparse
import configparser
import os

class visiondConfig:

    def __init__(self, config_file = './visiond.conf', arglist = []):
        self.config_file = config_file
        self.args = None
        self.setup(arglist)

    def setup(self, arglist):
        # Declare args parser
        self.parser = argparse.ArgumentParser(description='Visiond Video Streaming')

        # Setup common args
        self.parser.add_argument('--config', '-c', default=self.config_file, help="config file location, defaults to visiond directory")
        self.parser.add_argument('--camera_device', '-dev', help="Camera device, usually /dev/video0 for a single normal camera")
        self.parser.add_argument('--format', '-f', help="Camera format, if not specified here will autodetect yuv->mjpeg->h264")
        self.parser.add_argument('--pixelformat', '-p', help="Pixel Format, could be (fourcc)YUV2, I420, RGB etc")
        self.parser.add_argument('--encoder', '-e', help="Encoder, if not specified will default to h264.  Values are h264, mjpeg or none")
        self.parser.add_argument('--encoder_type', '-et', help="Encoder type, if not specified will fall back to sensible default for the encoder")
        self.parser.add_argument('--width', '-wt', default=640, help="Resolution width of video stream, must be valid for camera")
        self.parser.add_argument('--height', '-ht', default=480, help="Resolution height of video stream, must be valid for camera")
        self.parser.add_argument('--framerate', '-fr', default=30, help="Framerate of video stream, must be valid for camera")
        self.parser.add_argument('--brightness', '-b', default=0, help="Brightness - 0 is automatic")
        self.parser.add_argument('--input', '-i', default="v4l2", help="Stream input type: v4l2 (fpv), appsrc (cv), nvarguscamerasrc (nvidia jetson csi)")
        self.parser.add_argument('--output', '-o', default="rtsp", help="Stream output type: file (save video), udp (stream video), wcast (wifibroadcast), rtsp (rtsp server), webrtc (webrtc server")
        self.parser.add_argument('--output_dest', '-od', default="0.0.0.0", help="Output destination: filename (file output), IP address (udp/rtsp output), Interface (wcast output)")
        self.parser.add_argument('--output_port', '-op', default="5600", help="Output port: Port number (eg. 5000) for network destination, Channel for wifibroadcast output (eg. 1)")
        self.parser.add_argument('--bitrate', '-br', default='1000000', help="Target stream bitrate")
        self.parser.add_argument('--pipeline_override', '-po', help="Pipeline Override - This is used to provide a manual pipeline if the auto construction fails")
        self.parser.add_argument('--retry', '-r', default=10, help="Retry timeout - number of seconds visiond will wait before trying to recreate pipeline after error")
        self.parser.add_argument('--logdest', '-ld', default='both', help="Log destination - can be file, console or both (if run through systemd, console will log to system journal)")
        self.parser.add_argument('--logdir', '-li', default="/var/tmp/visiond", help="Log directory, if file logging set")
        self.parser.add_argument('--debug', '-d', help="Debug: Turns on gstreamer debug to the specified level.  Note level 4 and above is very verbose")
        self.parser.add_argument('--ssl_keyfile', '-sk', help="Set the path to SSL key for webrtc signalling server")
        self.parser.add_argument('--ssl_certfile', '-sc', help="Set the path to SSL cert for webrtc signalling server")

        self.args = self.parser.parse_args()

        # First parse config file, and set defaults
        defaults = {}
        if os.path.isfile(self.args.config):
            config = configparser.RawConfigParser()
            config.read([self.args.config])
            try:
                for key, value in config.items("Defaults"):
                    defaults[key] = self.get_config_value(config, "Defaults", key)
                self.parser.set_defaults(**defaults)
                self.args = self.parser.parse_args()
            except Exception as e:
                print("Error reading config file {}: {}".format(self.config_file, repr(e)))
                sys.exit(1)
        else:
            print("Error: Config file "+str(self.args.config)+" does not exist")
            sys.exit(1)

    # Return correctly typed config parser option
    def get_config_value(self, config, section, option):
        # Parses config value as python type
        try:
            return config.getint(section, option)
        except ValueError:
            pass
        try:
            return config.getfloat(section, option)
        except ValueError:
            pass
        try:
            return config.getboolean(section, option)
        except ValueError:
            pass
        return config.get(section, option)


if __name__ == "__main__": 
    print("Error: This should only be called as a module")
    sys.exit(1)

