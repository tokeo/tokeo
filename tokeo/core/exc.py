class TokeoBaseException(BaseException):
    """
    Base of all exceptions of tokeo.

    ### Notes

    : TokeoError derives from Exception and from this class, so it stays an
      Exception; an exception that only derives from this class, like the
      abort of a time limit, passes ```except Exception```

    : ```except TokeoBaseException``` catches both, e.g. in the main of an app

    : Keep this class empty, own slots would clash with bases like OSError

    """

    pass


class TokeoError(Exception, TokeoBaseException):
    """Generic errors."""

    pass
