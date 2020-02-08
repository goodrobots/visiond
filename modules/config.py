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
