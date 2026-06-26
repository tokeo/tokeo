import json
from datetime import date as date_type, datetime as datetime_type
import dataclasses
from . import date


class TokeoJsonEncoder:
    """
    Base JSON encoder for Tokeo types.

    Holds the ```default``` hook for ```json.dumps``` in ```encode```: json calls
    it for any value it cannot serialize itself. The base converts dates to
    standardized strings and unpacks a dataclass to a dict (so json walks its
    fields), and returns ```None``` for anything else (json renders ```null```).

    Subclasses change only the unknown-object case by overriding ```encode```
    and falling back to ```super().encode(obj)``` for the handled types.

    ### Notes

    - datetime objects are checked first and converted via
        date.to_utc_timestring() to 'YYYY-MM-DD HH:MM:SS.MMMZ' format
    - date objects are converted to 'YYYY-MM-DD' format
    - a dataclass is unpacked one level via __dict__ so json recurses through its
        fields -- not dataclasses.asdict, which deepcopies the whole graph and
        dies on a field holding a live object (one json cannot copy or serialize)

    """

    def encode(self, obj):
        """
        Encode one Tokeo type for json, or signal it is unknown.

        ### Args

        - **obj** (any): The object json could not serialize itself

        ### Returns

        - **str|dict|None**: A string for a date/datetime; a shallow dict for a
            dataclass; ```None``` for any other object (json renders ```null```)

        """
        # check datetime before date: datetime is a subclass of date, so a
        # date-first test would also catch datetimes and drop their time part
        if isinstance(obj, datetime_type):
            return date.to_utc_timestring(obj)
        # a plain date carries no time or tzinfo, so format it directly; routing
        # it through as_utc() would raise since as_utc only accepts str/datetime
        if isinstance(obj, date_type):
            return obj.strftime('%Y-%m-%d')
        # a dataclass: hand json a shallow dict of its fields so json recurses
        # through them (not asdict, which deepcopies and dies on a live field)
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return dict(obj.__dict__)
        # an unknown object: None, so json renders it as null
        return None


class TokeoJsonUnknownNoneEncoder(TokeoJsonEncoder):
    """
    Encoder that renders an unknown object as ```null``` (the default).

    Identical to ```TokeoJsonEncoder```; named for use as the explicit default
    in ```json_dump``` and to make the unknown-object policy readable at the
    call site.

    """

    pass


class TokeoJsonUnknownNameEncoder(TokeoJsonEncoder):
    """
    Encoder that renders an unknown object as its type name.

    For a readable placeholder instead of ```null``` -- for an object that is an
    attribution or a marker rather than data, where its type name says more than
    ```null``` does.

    """

    def encode(self, obj):
        """
        Encode like the base, but name an unknown object instead of nulling it.

        ### Args

        - **obj** (any): The object json could not serialize itself

        ### Returns

        - **str|dict**: As the base for a handled type; the object's type name
            (a string) for any other object

        """
        # the base returns None exactly for an object it does not handle; check
        # for None (not falsiness), so a handled but falsy value like an empty
        # dataclass dict stays itself instead of being replaced by a type name
        result = super().encode(obj)
        return result if result is not None else type(obj).__name__


def json_dump(obj, encoder=None, encoding=None, **kwargs):
    """
    Serialize an object to a JSON string with Tokeo-specific type handling.

    A wrapper around ```json.dumps``` that routes unserializable values through
    a ```TokeoJsonEncoder```, so dates and dataclasses survive, and optionally
    encodes the result to bytes.

    ### Args

    - **obj** (any): The object to serialize
    - **encoder** (TokeoJsonEncoder, optional): The encoder whose ```encode``` is
        the json ```default``` hook; defaults to ```TokeoJsonUnknownNoneEncoder```
        (an unknown object becomes ```null```). Pass the name encoder for
        type-name placeholders, or a custom subclass
    - **encoding** (str, optional): If given, encode the JSON string to bytes
        with this encoding (e.g. 'utf-8')
    - ****kwargs**: Forwarded to ```json.dumps``` (e.g. indent, sort_keys)

    ### Returns

    - **str|bytes**: The JSON string, or bytes when ```encoding``` is given

    """
    encoder = encoder if encoder else TokeoJsonUnknownNoneEncoder()
    j = json.dumps(obj, default=encoder.encode, **kwargs)
    return j.encode(encoding) if encoding else j
