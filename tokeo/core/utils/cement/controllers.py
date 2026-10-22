def subcmdmeta(controller):
    """
    Access the \\_\\_cement_meta\\_\\_ information of subcommand within the command.

    ### Args

    - **controller** (Controller): The controller instance

    ### Returns

    - **\\_\\_cement_meta\\_\\_** (CommandMeta|None): object ref to Meta
        information of the subcommand, None outside a dispatched subcommand

    ### Notes

    : Delegates to ```_command_meta``` of the cement controller (3.0.16+)

    """
    return controller._command_meta


def defaultmeta(controller):
    """
    Access the \\_\\_cement_meta\\_\\_ information of default subcommand within the
    command.

    ### Args

    - **controller** (Controller): The controller instance

    ### Returns

    - **\\_\\_cement_meta\\_\\_** (CommandMeta|None): object ref to Meta
        information of the _default_ subcommand, None when ```default_func```
        is not an exposed command

    ### Notes

    : Delegates to ```_default_command_meta``` of the cement controller (3.0.16+)

    """
    return controller._default_command_meta


def log_info_help(controller):
    """
    Log the help (dry) from subcmd's \\_\\_cement_meta\\_\\_.

    ### Args

    - **controller** (Controller): The controller instance

    ### Output

    :  Log the controller's ```Meta.help``` to the application info logger

    """
    controller.app.log.info(subcmdmeta(controller).parser_options['help'])
