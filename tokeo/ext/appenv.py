"""
Environment management extension for Tokeo applications.

This module provides environment detection and configuration based on
environment variables. It supports different runtime environments like
production, staging, development, and testing.

When the appenv extension is used, the normal cement config file behavior
is replaced by tokeo's environment based configuration. A cement
runtime configuration is built from base plus a detected environment.

### Example

A detected environment is also easily usable in own conditions like:

```python
# Check current environment
if app.env.IS_DEV_MODE:
    # Development-specific code
    pass
```

"""

import os
from cement.utils import fs
import glob
from tokeo.core.utils.sort import sort_key_by_lex_and_num_ordered


# Environment constants
PRODUCTION = 'production'
STAGING = 'staging'
DEVELOPMENT = 'development'
TESTING = 'testing'
ENVIRONMENTS = (
    PRODUCTION,
    STAGING,
    DEVELOPMENT,
    TESTING,
)


class TokeoAppEnv:
    """
    Application environment manager for Tokeo applications.

    Detects environment based on environment variables and app label.
    Loads configuration files and provides environment flags for different
    runtime contexts.

    ### Attributes

    - **APP_LABEL** (str): The lowercase application label
    - **APP_ENV_VAR_NAME** (str): The environment variable name for app environment
    - **APP_ENV_VAR_VALUE** (str): The value of the environment variable
    - **APP_ENV** (str): The detected environment (production, development, staging,
        testing) or None if no environment could be detected
    - **IS_PROD_MODE** (bool): True if running in production environment
    - **IS_STAGE_MODE** (bool): True if running in staging environment
    - **IS_DEV_MODE** (bool): True if running in development environment
    - **IS_TEST_MODE** (bool): True if running in testing environment
    - **APP_MAIN_DIR** (str): The main directory of the application
    - **APP_DIR** (str): The base directory of the application
    - **APP_CONFIG_DIR** (str): The configuration directory of the application

    """

    def __init__(self, app):
        """
        Initialize the environment manager.

        ### Args

        - **app** (Application): The Cement application instance

        ### Notes

        - Detects environment from environment variables or app name suffix
        - Configures application paths and directories
        - Includes environment-specific configuration files
        - Handles the cement test app class in a specific manner. Replaces
            the "_test" suffix to allow easily the usage for testing configuration
            in the same place
        - If no environment is detected (APP_ENV is None), only the base config
            files are loaded and all IS_*_MODE flags are False

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
            # an "<app>_test" app runs automatically in testing mode but
            # reuses its base app identity. The "_test" suffix gets stripped
            # so label, env var name and (most important) config dir will
            # resolve and align to "<app>" instead of "<app>_test".
            # Otherwise it would be necessary to have a different testing
            # config dir and files when running tests by cements test app.
            app._meta.label = self.APP_LABEL[: -len('_test')]
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
        self.APP_DIR = fs.abspath(os.path.join(self.APP_MAIN_DIR, '..'))
        # build the config dir
        self.APP_CONFIG_DIR = fs.abspath(os.path.join(self.APP_DIR, 'config', self.APP_LABEL))
        # add base configs
        app._meta.config_files = self.get_config_files(app_config_file_suffix=app._meta.config_file_suffix)
        # add environment configs on top of base, but only if an
        # environment was actually detected (APP_ENV is not None)
        if self.APP_ENV:
            app._meta.config_files.extend(
                self.get_config_files(app_env=self.APP_ENV, app_config_file_suffix=app._meta.config_file_suffix),
            )

    def get_config_files(self, app_env='base', app_config_file_suffix='.yaml'):
        """
        Resolve all config files for a single environment.

        Builds the file list for the given environment: the top-level
        <env> config first, then any partial configs found recursively in
        the <env>.d folder (ordered lexicographically and with numbers
        respected). .local files are appended last and are skipped entirely
        for the base environment.

        ### Args

        - **app_env** (str): The id of the selected environment (base or other)
        - **app_config_file_suffix** (str): The suffix for the config files.

        ### Returns

        - **list**: List of configuration file paths, with the base and
            environment config files first, followed by any .local config files

        """

        # define empty arrays for env and local configurations
        configs = []
        local_configs = []

        # main env config is added unconditionally
        # a missing file will be skipped by the config reader
        configs.append(os.path.join(self.APP_CONFIG_DIR, f'{app_env}{app_config_file_suffix}'))

        # partial environment config files in .d directories
        files = glob.glob(os.path.join(self.APP_CONFIG_DIR, f'{app_env}.d', '**', f'*{app_config_file_suffix}'), recursive=True)
        # sort the files by lexicographically order and
        # respect numbers in strings while ordering
        files.sort(key=sort_key_by_lex_and_num_ordered)
        # loop files and group by standard and local config file
        for f in files:
            # test for .local config file
            if f.endswith(f'.local{app_config_file_suffix}'):
                # .local overrides are only collected for real environments
                # base intentionally ignores any .local files
                if app_env != 'base':
                    local_configs.append(f)
            else:
                # add config file
                configs.append(f)

        # return standard configs first, then local configs appended
        return [*configs, *local_configs]


def load(app):
    """
    Load the environment extension into the application.

    This function is called by the Cement framework when loading extensions.
    Extends the application with environment detection and configuration.

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

    : Creates a new TokeoAppEnv instance and attaches it to the application
        as `app.env`, making environment information available throughout the app.

    """
    app.extend('env', TokeoAppEnv(app))
