subcmdmeta = lambda a: getattr(a, a.app.pargs.__dispatch__.split('.')[1]).__cement_meta__
"""
access the __cement_meta__ information of subcommand within the command
"""

defaultmeta = lambda a: getattr(a, a._meta.default_func).__cement_meta__
"""
access the __cement_meta__ information of default subcommand within the command
"""


def controller_log_info_help(controller):
    controller.app.log.info(subcmdmeta(controller).parser_options['help'])
