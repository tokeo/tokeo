"""
Hands out the version of tokeo, counted by cement like the build does.
"""

from cement.utils.version import get_version as cement_get_version

#: The version of tokeo as (major, minor, patch, release, build)
VERSION = (2, 2, 0, 'final', 0)


def get_version(version=VERSION):
    """
    Hand out the version as string, as cement counts it for the build.

    ### Args

    - **version** (tuple, optional): Major, minor, patch, release and serial.
        Defaults to ```VERSION```.

    ### Returns

    - **str**: The version, e.g. ```2.2.0```, or ```2.2.0b1``` for a beta

    """
    return cement_get_version(version)
