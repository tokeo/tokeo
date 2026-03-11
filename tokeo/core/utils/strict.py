"""
The strict  module to ensure that some warnings identified
as exceptions for security reasons.

Just import to the main stage

"""

# force all SyntaxWarnings to raise an exception immediately
import warnings
warnings.simplefilter('error', SyntaxWarning)
