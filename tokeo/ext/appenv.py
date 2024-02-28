import os
from cement.utils import fs


PRODUCTION = 'production'
STAGING = 'staging'
DEVELOPMENT = 'development'
TESTING = 'testing'


class TokeoAppEnv:

    def __init__(self, app):
        # get the app label
        self.APP_LABEL = app._meta.label.strip().lower()
        # build the main ENV_VAR_NAME from app meta label
        self.APP_ENV_VAR_NAME = self.APP_LABEL.upper() + '_ENV'
        # get the started ENV
        self.APP_ENV_VAR_VALUE = os.environ.get(self.APP_ENV_VAR_NAME, default='').strip().lower()
        # check the started ENV
        if self.APP_ENV_VAR_VALUE in ['dev', 'development']:
            self.APP_ENV = DEVELOPMENT
        elif self.APP_ENV_VAR_VALUE in ['prod', 'production']:
            self.APP_ENV = PRODUCTION
        elif self.APP_ENV_VAR_VALUE in ['stage', 'staging']:
            self.APP_ENV = STAGING
        elif self.APP_ENV_VAR_VALUE in ['test', 'testing']:
            self.APP_ENV = TESTING
        else:
            self.APP_ENV = None
        # identify MODES
        self.IS_PROD_MODE = self.APP_ENV == PRODUCTION
        self.IS_STAGE_MODE = self.APP_ENV == STAGING
        self.IS_DEV_MODE = self.APP_ENV == DEVELOPMENT
        self.IS_TEST_MODE = self.APP_ENV == TESTING
        # get the main dir from meta
        self.APP_MAIN_DIR = app._meta.main_dir
        # build the base app dir relatively from __main__
        self.APP_DIR = fs.abspath(self.APP_MAIN_DIR + '/..')
        # build the config dir
        self.APP_CONFIG_DIR = fs.abspath(self.APP_DIR + '/config')
        # add environment configs
        if self.APP_ENV:
            config_filenames = [
                self.APP_LABEL,
                self.APP_LABEL + '.' + self.APP_ENV,
                self.APP_LABEL + '.' + self.APP_ENV + '.local',
            ]
        else:
            config_filenames = [
                self.APP_LABEL,
            ]
        # load the config from appenv files
        app._meta.config_files = list()
        for config_filename in config_filenames:
            app._meta.config_files.append(f'{self.APP_CONFIG_DIR}/{config_filename}{app._meta.config_file_suffix}')


def load(app):
    app.extend('env', TokeoAppEnv(app))
