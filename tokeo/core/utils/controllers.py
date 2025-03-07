def subcmdmeta(controller):
    """
    Access the __cement_meta__ information of subcommand within the command.

    Args:
        controller: The controller instance

    Returns:
        The __cement_meta__ information of the subcommand
    """
    return getattr(controller, controller.app.pargs.__dispatch__.split('.')[1]).__cement_meta__


def defaultmeta(controller):
    """
    Access the __cement_meta__ information of default subcommand within the command.

    Args:
        controller: The controller instance

    Returns:
        The __cement_meta__ information of the default subcommand
    """
    return getattr(controller, controller._meta.default_func).__cement_meta__


def controller_log_info_help(controller):
    controller.app.log.info(subcmdmeta(controller).parser_options['help'])
