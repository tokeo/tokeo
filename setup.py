from setuptools import setup, find_packages
from cedra.core.version import get_version

VERSION = get_version()

f = open('README.md', 'r')
LONG_DESCRIPTION = f.read()
f.close()

setup(
    name='cedra',
    version=VERSION,
    description='The Cedra CLI contains all the dramatiq workers and publishers.',
    long_description=LONG_DESCRIPTION,
    long_description_content_type='text/markdown',
    author='Tom Freudenberg',
    author_email='th.freudenberg@gmail.com',
    url='about:none',
    license='MIT',
    packages=find_packages(exclude=['ez_setup', 'tests*']),
    package_data={'cedra': ['templates/*']},
    include_package_data=True,
    entry_points="""
        [console_scripts]
        cedra = cedra.main:main
    """,
)
