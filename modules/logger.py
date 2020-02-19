import os
import logging
import sys

class visiondLogger():
    def __init__(self, cwd, config):
        self.cwd = cwd
        self.config = config
        self.handle = self.setup_logger()

    def setup_logdir(self):
        if 'logdir' in self.config.args:
            self.logdir = self.config.args.logdir
        else:
            self.logdir = os.path.join(self.cwd, 'logs')
        print("Using log directory: {}".format(self.logdir))
        if not os.path.exists(self.logdir):
            os.makedirs(self.logdir)

    def setup_logger(self):

        if 'logdest' in self.config.args:
            self.logdest = self.config.args.logdest
        else:
            self.logdest = 'both'

        root = logging.getLogger('visiond')
        root.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        if self.logdest == 'both' or self.logdest == 'file':
            self.setup_logdir()
            fhandler = logging.FileHandler(os.path.join(self.logdir, "visiond.log"))
            fhandler.setLevel(logging.DEBUG)
            fhandler.setFormatter(formatter)
            root.addHandler(fhandler)

        if self.logdest == 'both' or self.logdest == 'console':
            chandler = logging.StreamHandler(sys.stdout)
            chandler.setLevel(logging.DEBUG)
            chandler.setFormatter(formatter)
            root.addHandler(chandler)

        return root