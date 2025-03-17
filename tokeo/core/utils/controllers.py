def subcmdmeta(controller):
    """
    Access the \\_\\_cement_meta\\_\\_ information of subcommand within the command.

    ### Args:

    - **controller** (Controller): The controller instance

    ### Returns:

    - **\\_\\_cement_meta\\_\\_** (Meta): object ref to Meta information of the
      subcommand

    """
    return getattr(controller, controller.app.pargs.__dispatch__.split('.')[1]).__cement_meta__


def defaultmeta(controller):
    """
    Access the \\_\\_cement_meta\\_\\_ information of default subcommand within the
    command.

    ### Args:

    - **controller** (Controller): The controller instance

    ### Returns:

    - **\\_\\_cement_meta\\_\\_** (Meta): object ref to Meta information of the
      _default_ subcommand

    """
    return getattr(controller, controller._meta.default_func).__cement_meta__


def controller_log_info_help(controller):
    """
    Log the help (dry) from subcmd's \\_\\_cement_meta\\_\\_.

    ### Args:

    - **controller** (Controller): The controller instance

    ### Output:

    :  Log the controller's `Meta`.`help` to the application info logger

    """
    controller.app.log.info(subcmdmeta(controller).parser_options['help'])
