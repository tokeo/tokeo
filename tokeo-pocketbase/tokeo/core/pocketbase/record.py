"""
The records of PocketBase as dicts that read and write their fields also as
attributes, and a page of records with its counts.

"""


class TokeoPocketBaseRecord(dict):
    """
    A record of PocketBase, a dict that also reads and writes its fields as
    attributes.

    ### Notes

    : ```record.title``` and ```record['title']``` are the same; a field
        named like a dict method, e.g. ```items```, is only reached by
        ```record['items']```

    : The records in ```expand``` are records as well

    """

    def __init__(self, data=None):
        super().__init__(data or {})
        # the expanded relations are records too, one or a list of them
        expand = self.get('expand')
        if isinstance(expand, dict):
            self['expand'] = TokeoPocketBaseRecord(
                {
                    key: [TokeoPocketBaseRecord(item) for item in value] if isinstance(value, list) else TokeoPocketBaseRecord(value)
                    for key, value in expand.items()
                }
            )

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        self[name] = value


class TokeoPocketBaseListResult:
    """
    A page of records.

    ### Args

    - **data** (dict): The answer of PocketBase for a list of records

    ### Notes

    : ```total_items``` and ```total_pages``` are -1 when the count was
        skipped by ```skip_total```

    """

    def __init__(self, data):
        #: The number of this page, from 1
        self.page = data.get('page', 1)
        #: The number of records per page
        self.per_page = data.get('perPage', 0)
        #: The number of records of all pages, -1 when skipped
        self.total_items = data.get('totalItems', -1)
        #: The number of pages, -1 when skipped
        self.total_pages = data.get('totalPages', -1)
        #: The records of this page
        self.items = [TokeoPocketBaseRecord(item) for item in data.get('items') or []]


# a placeholder of a filter: {:name}
