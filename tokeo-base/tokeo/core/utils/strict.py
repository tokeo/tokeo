"""
The strict module ensures that certain warnings are treated as
exceptions for safety reasons.

Just import it early during application startup.

"""

# force all SyntaxWarnings to raise an exception immediately
import warnings

warnings.simplefilter('error', SyntaxWarning)
