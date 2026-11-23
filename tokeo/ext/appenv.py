"""
Environment management extension for Tokeo applications.

This module provides environment detection and configuration based on
environment variables. It supports different runtime environments like
production, staging, development, and testing.

The variable ```<APP_LABEL>_ENV``` names the environment with the values of
```NODE_ENV``` and Rails: ```development```, ```staging```, ```production```
or ```test```, e.g. ```SHOP_ENV=test```. ```testing``` and the short names
```dev```, ```stage``` and ```prod``` work too. The config files carry the long
name of the environment, e.g. ```testing.yaml```.

When the appenv extension is used, the normal cement config file behavior
is replaced by tokeo's environment based configuration. A cement
runtime configuration is built from base plus a detected environment.

The local config of a machine may stay outside of the app, in the folder
```<label>.local_config_dir``` of the config names, e.g.
```/etc/<label>/config/```; the variable ```<APP_LABEL>_LOCAL_CONFIG_DIR```
overrides it. Only its .local files of the environment are read,
```<environment>.local.yaml``` and the ```*.local.yaml``` right in
```<environment>.d```, after the config of the app, so they win.

As this config may hold secrets, the folder must be an absolute path outside
of the config dir of the app, nothing is read deeper than ```<environment>.d```
and no file may link into that config dir. Like ssh, its folders and files
give no rights to group and others, e.g. 700 and 600, and a set folder without
any config of the environment raises.

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
import stat
from cement.utils import fs
import glob
from tokeo.core.exc import TokeoError
from tokeo.core.utils.sort import get_natural_sort_key

#: The environment of production
PRODUCTION = 'production'
#: The environment of staging
STAGING = 'staging'
#: The environment of development
DEVELOPMENT = 'development'
#: The environment of testing
TESTING = 'testing'
#: All environments, from production to testing
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

    ### Args

    - **app** (Application): The Cement application instance

    ### Notes

    - Detects environment from environment variables or app name suffix
    - Configures application paths and directories
    - Includes environment-specific configuration files
    - Handles the cement test app class in a specific manner. Strips the
        "_test" suffix to use the config of the app, and runs it always in
        testing, whatever ```<APP_LABEL>_ENV``` says
    - If no environment is detected (APP_ENV is None), only the base config
        files are loaded and all IS_*_MODE flags are False
    - Removes the config files and dirs cement reads by itself, e.g.
        ```/etc/<label>/<label>.yaml```, ```~/.config/<label>/``` and
        ```~/.<label>.yaml```; only the files of ```config/<label>``` are read
    - Reads the .local files of the environment from ```local_config_dir```
        last, the local config of the machine outside of the app

    ### Raises

    - **TokeoError**: If the environment variable names no environment,
        e.g. a typo like ```prd```, or when ```local_config_dir``` breaks a
        rule of the local config of the machine

    """

    #: (str|None) The detected environment (production, development, staging,
    #: testing) or None if no environment could be detected
    APP_ENV = None

    def __init__(self, app):
        # get the app label
        #: The lowercase application label
        self.APP_LABEL = app._meta.label.strip().lower()
        # an "<app>_test" app reuses the identity of its base app: the
        # "_test" suffix gets stripped, so label, env var name and (most
        # important) config dir resolve to "<app>" instead of "<app>_test"
        is_test_app = self.APP_LABEL.endswith('_test')
        if is_test_app:
            app._meta.label = self.APP_LABEL = self.APP_LABEL[: -len('_test')]
        # build the main ENV_VAR_NAME from app meta label
        #: The environment variable name for app environment
        self.APP_ENV_VAR_NAME = self.APP_LABEL.upper() + '_ENV'
        # get the started ENV, a test app runs always in testing, a set
        # "<APP>_ENV=prod" of the shell must not run the tests on production
        if is_test_app:
            #: The value of the environment variable
            self.APP_ENV_VAR_VALUE = 'test'
        else:
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
        elif self.APP_ENV_VAR_VALUE:
            # a typo must not run with the base config only
            raise TokeoError(
                f'{self.APP_ENV_VAR_NAME}={self.APP_ENV_VAR_VALUE!r} is no environment, '
                'use dev, development, prod, production, stage, staging, test or testing'
            )
        else:
            self.APP_ENV = None
        # identify MODES
        #: True if running in production environment
        self.IS_PROD_MODE = self.APP_ENV == PRODUCTION
        #: True if running in staging environment
        self.IS_STAGE_MODE = self.APP_ENV == STAGING
        #: True if running in development environment
        self.IS_DEV_MODE = self.APP_ENV == DEVELOPMENT
        #: True if running in testing environment
        self.IS_TEST_MODE = self.APP_ENV == TESTING
        # get the main dir from meta
        #: The main directory of the application
        self.APP_MAIN_DIR = app._meta.main_dir
        # build the base app dir relatively from __main__
        #: The base directory of the application
        self.APP_DIR = fs.abspath(os.path.join(self.APP_MAIN_DIR, '..'))
        # build the config dir
        #: The configuration directory of the application
        self.APP_CONFIG_DIR = fs.abspath(os.path.join(self.APP_DIR, 'config', self.APP_LABEL))
        # remove all default cement config loadings
        app._meta.core_system_config_files = []
        app._meta.core_system_config_dirs = []
        app._meta.core_user_config_files = []
        app._meta.core_user_config_dirs = []
        # add base configs
        app._meta.config_files = self.get_config_files(app_config_file_suffix=app._meta.config_file_suffix)
        # add environment configs on top of base, but only if an
        # environment was actually detected (APP_ENV is not None)
        if self.APP_ENV:
            app._meta.config_files.extend(
                self.get_config_files(app_env=self.APP_ENV, app_config_file_suffix=app._meta.config_file_suffix),
            )

            # the local config of the machine comes after the config files of
            # the app: its folder is a value of that config, so it is read when
            # cement validates the config, before any handler uses it
            _app_validate_config = app.validate_config

            def appenv_validate_config():
                for f in self.get_local_config_files(
                    app_env=self.APP_ENV,
                    app_config_file_suffix=app._meta.config_file_suffix,
                    local_config_dir=app.config.get(app._meta.config_section, 'local_config_dir', fallback=None),
                ):
                    # cement has parsed its list already, so the file is added
                    # to the list and parsed here
                    app.add_config_file(f)
                    app.config.parse_file(f)
                # then the validation of the app as before
                _app_validate_config()

            app.validate_config = appenv_validate_config

    def get_local_config_files(self, app_env='base', app_config_file_suffix='.yaml', local_config_dir=None):
        """
        Resolve the local config files of the machine for an environment.

        Only the .local files of the environment count: ```<env>.local.yaml```
        first, then the ```*.local.yaml``` right in ```<env>.d``` (ordered
        lexicographically and with numbers respected), no deeper. Any other
        file of the folder is not read.

        ### Args

        - **app_env** (str): The id of the selected environment
        - **app_config_file_suffix** (str): The suffix for the config files.
        - **local_config_dir** (str|None): The folder of the local config of the
            machine, outside of the app

        ### Returns

        - **list**: List of the local config file paths, empty for base or
            without a folder

        ### Raises

        - **TokeoError**: If the folder is no absolute path, is or lies in the
            config dir of the app, a file links into it, a folder or file gives
            rights to group or others or cannot be read, or there is no config
            of the environment

        """
        # when base or without a folder there is nothing to read
        if app_env == 'base' or not local_config_dir:
            return []
        # only an absolute folder outside of the config dir of the app
        if not isinstance(local_config_dir, str) or not os.path.isabs(local_config_dir):
            raise TokeoError(f'{self.APP_LABEL}.local_config_dir must be an absolute path, not {local_config_dir!r}')
        if self._is_in_config_dir(local_config_dir):
            raise TokeoError(f'{self.APP_LABEL}.local_config_dir must lie outside of the config dir of the app, not {local_config_dir!r}')
        local_config_dir = os.path.realpath(local_config_dir)
        if not os.path.isdir(local_config_dir):
            raise TokeoError(f'{self.APP_LABEL}.local_config_dir must be a folder, not {local_config_dir!r}')
        # check the folders before listing them
        env_dir = os.path.join(local_config_dir, f'{app_env}.d')
        for d in [local_config_dir, *([env_dir] if os.path.isdir(env_dir) else [])]:
            self._check_permissions(d)
        # the local config of the environment, if it is there
        local_config = os.path.join(local_config_dir, f'{app_env}.local{app_config_file_suffix}')
        # the partial local config files right in its .d directory
        local_configs = glob.glob(os.path.join(env_dir, f'*.local{app_config_file_suffix}'))
        # sort the files by lexicographically order and
        # respect numbers in strings while ordering
        local_configs.sort(key=get_natural_sort_key)
        files = [*([local_config] if os.path.isfile(local_config) else []), *local_configs]
        # a set folder holds a config of the environment
        if not files:
            raise TokeoError(
                f'{self.APP_LABEL}.local_config_dir must hold {app_env}.local{app_config_file_suffix} '
                f'or {app_env}.d/*.local{app_config_file_suffix}, not {local_config_dir!r}'
            )
        # check the files, a link may lead into the config dir of the app
        for f in files:
            if self._is_in_config_dir(f):
                raise TokeoError(f'{self.APP_LABEL}.local_config_dir must not link into the config dir of the app, not {f!r}')
            self._check_permissions(f)
        return files

    def _is_in_config_dir(self, path):
        # the path, or the target of its link, is the config dir of the app or in it
        config_dir = os.path.realpath(self.APP_CONFIG_DIR)
        return os.path.commonpath([os.path.realpath(path), config_dir]) == config_dir

    def _check_permissions(self, path):
        # like ssh: no rights for group and others, and readable by the app
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode & 0o077:
            raise TokeoError(f'{self.APP_LABEL}.local_config_dir must give no rights to group and others, not mode {mode:03o} of {path!r}')
        if not os.access(path, (os.R_OK | os.X_OK) if os.path.isdir(path) else os.R_OK):
            raise TokeoError(f'{self.APP_LABEL}.local_config_dir must be readable by the app, not {path!r}')

    def get_config_files(self, app_env='base', app_config_file_suffix='.yaml'):
        """
        Resolve all config files for a single environment.

        Builds the file list for the given environment: the top-level
        <env> config first, then any partial configs found recursively in
        the <env>.d directory (ordered lexicographically and with numbers
        respected). .local files are appended last and are skipped entirely
        for the base environment.

        ### Args

        - **app_env** (str): The id of the selected environment (base or other)
        - **app_config_file_suffix** (str): The suffix for the config files.

        ### Returns

        - **list**: List of configuration file paths, with the base and
            environment config files first, followed by any .local config files

        ### Notes

        : Even when added, a missing file will be skipped by the config reader

        """

        # define and initialize arrays for env and local configurations
        # main env configs are added always if exists but .local not for base
        f = os.path.join(self.APP_CONFIG_DIR, f'{app_env}{app_config_file_suffix}')
        configs = [f] if os.path.isfile(f) else []
        f = os.path.join(self.APP_CONFIG_DIR, f'{app_env}.local{app_config_file_suffix}')
        local_configs = [f] if app_env != 'base' and os.path.isfile(f) else []

        # partial environment config files in .d directories
        files = glob.glob(os.path.join(self.APP_CONFIG_DIR, f'{app_env}.d', '**', f'*{app_config_file_suffix}'), recursive=True)
        # sort the files by lexicographically order and
        # respect numbers in strings while ordering
        files.sort(key=get_natural_sort_key)
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
        as ```app.env```, making environment information available throughout
        the app.

    """
    app.extend('env', TokeoAppEnv(app))
