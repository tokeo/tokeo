"""
Environment management extension for Tokeo applications.

This module provides environment detection and configuration based on
environment variables. It supports different runtime environments like
production, staging, development, and testing.

Usage Example:
    ```python
    # Automatically loaded by Cement framework via hooks
    # Access environment information through app.env

    # Check current environment
    if app.env.IS_DEV_MODE:
        # Development-specific code
        pass

    # Get environment-specific configurations
    app.config.get('my_section', 'my_key')  # Uses environment-specific config
    ```
"""

import os
from cement.utils import fs


# Environment constants
PRODUCTION = 'production'
STAGING = 'staging'
DEVELOPMENT = 'development'
TESTING = 'testing'


class TokeoAppEnv:
    """
    Application environment manager for Tokeo applications.

    Detects environment based on environment variables and app label.
    Loads configuration files and provides environment flags.

    Attributes:
        APP_LABEL (str): The lowercase application label.
        APP_ENV_VAR_NAME (str): The environment variable name for app
            environment.
        APP_ENV_VAR_VALUE (str): The value of the environment variable.
        APP_ENV (str): The detected environment (production, development,
            staging, testing).
        IS_PROD_MODE (bool): True if running in production environment.
        IS_STAGE_MODE (bool): True if running in staging environment.
        IS_DEV_MODE (bool): True if running in development environment.
        IS_TEST_MODE (bool): True if running in testing environment.
        APP_MAIN_DIR (str): The main directory of the application.
        APP_DIR (str): The base directory of the application.
        APP_CONFIG_DIR (str): The configuration directory of the application.
    """

    def __init__(self, app):
        """
        Initialize the environment manager.

        Args:
            app: The Cement application instance.
        """
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
        elif self.APP_LABEL.endswith('_test'):
            # check if _test app was called
            app._meta.label = self.APP_LABEL[:-5]
            self.APP_LABEL = app._meta.label
            self.APP_ENV_VAR_NAME = self.APP_LABEL.upper() + '_ENV'
            self.APP_ENV_VAR_VALUE = 'test'
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
    """
    Load the environment extension into the application.

    This function is called by the Cement framework when loading extensions.

    Args:
        app: The Cement application instance.
    """
    app.extend('env', TokeoAppEnv(app))
