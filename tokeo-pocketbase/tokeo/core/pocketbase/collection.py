"""
Tokeo PocketBase Collections.

The record methods of one collection: list, page through, find, get,
create, update and delete, with files along with the fields.

"""

import json
from .exc import TokeoPocketBaseError
from .record import Record, ListResult
from .filter import path_segment


class Collection:
    """
    The record methods of one collection.

    ### Args

    - **client** (PocketBase): The client that sends the requests
    - **collection** (str): The name or id of the collection

    ### Notes

    : ```query``` takes further query parameters of PocketBase; a key there
        wins over the arguments

    """

    def __init__(self, client, collection):
        #: The client that sends the requests
        self.client = client
        #: The name or id of the collection
        self.collection = collection
        self._path = f'/api/collections/{path_segment(collection)}/records'

    def _record_path(self, record_id):
        # the path of one record
        return f'{self._path}/{path_segment(record_id)}'

    def get_list(self, page=1, per_page=30, filter=None, sort=None, expand=None, fields=None, skip_total=False, query=None):
        """
        Get a page of records.

        ### Args

        - **page** (int): The page, from 1
        - **per_page** (int): The records per page
        - **filter** (str): The filter, e.g. built by ```filter()```
        - **sort** (str): The sort order, e.g. ```-created,title```
        - **expand** (str): The relations to expand, e.g. ```owner```
        - **fields** (str): The fields to return, e.g. ```id,title```
        - **skip_total** (bool): Skip counting all records, faster
        - **query** (dict): Further query parameters

        ### Returns

        - **ListResult**: The records of the page and the counts

        """
        params = {
            'page': page,
            'perPage': per_page,
            'filter': filter or None,
            'sort': sort or None,
            'expand': expand or None,
            'fields': fields or None,
            'skipTotal': 'true' if skip_total else None,
            **(query or {}),
        }
        return ListResult(self.client.send('GET', self._path, params=params))

    def get_full_list(self, per_page=500, filter=None, sort=None, expand=None, fields=None, query=None):
        """
        Get all records, page by page.

        ### Args

        - **per_page** (int): The records per request
        - **filter** (str): The filter, e.g. built by ```filter()```
        - **sort** (str): The sort order
        - **expand** (str): The relations to expand
        - **fields** (str): The fields to return
        - **query** (dict): Further query parameters

        ### Returns

        - **list**: All records that match

        """
        records = []
        page = 1
        while True:
            result = self.get_list(page, per_page, filter, sort, expand, fields, skip_total=True, query=query)
            records.extend(result.items)
            if len(result.items) < per_page:
                return records
            page += 1

    def get_first(self, filter, sort=None, expand=None, fields=None, query=None):
        """
        Get the first record that matches a filter.

        ### Args

        - **filter** (str): The filter, e.g. built by ```filter()```
        - **sort** (str): The sort order
        - **expand** (str): The relations to expand
        - **fields** (str): The fields to return
        - **query** (dict): Further query parameters

        ### Returns

        - **Record**: The first record

        ### Raises

        - **TokeoPocketBaseError**: With status 404 if no record matches

        """
        result = self.get_list(1, 1, filter, sort, expand, fields, skip_total=True, query=query)
        if not result.items:
            raise TokeoPocketBaseError(f'no record of {self.collection} matches {filter!r}', status=404)
        return result.items[0]

    def get_one(self, record_id, expand=None, fields=None, query=None):
        """
        Get one record by its id.

        ### Args

        - **record_id** (str): The id of the record
        - **expand** (str): The relations to expand
        - **fields** (str): The fields to return
        - **query** (dict): Further query parameters

        ### Returns

        - **Record**: The record

        ### Raises

        - **TokeoPocketBaseError**: With status 404 if the record is unknown

        """
        params = {'expand': expand or None, 'fields': fields or None, **(query or {})}
        return Record(self.client.send('GET', self._record_path(record_id), params=params))

    def _write(self, method, path, data, files, expand, fields, query):
        # json, or multipart with the fields as @jsonPayload when files go along
        params = {'expand': expand or None, 'fields': fields or None, **(query or {})}
        if files:
            answer = self.client.send(method, path, params=params, data={'@jsonPayload': json.dumps(data or {})}, files=files)
        else:
            answer = self.client.send(method, path, params=params, json=data or {})
        return Record(answer)

    def create(self, data=None, files=None, expand=None, fields=None, query=None):
        """
        Create a record.

        ### Args

        - **data** (dict): The fields of the record
        - **files** (list): Files to upload, as ```(field, (filename,
            content, content_type))``` tuples
        - **expand** (str): The relations to expand in the answer
        - **fields** (str): The fields to return
        - **query** (dict): Further query parameters

        ### Returns

        - **Record**: The new record

        """
        return self._write('POST', self._path, data, files, expand, fields, query)

    def update(self, record_id, data=None, files=None, expand=None, fields=None, query=None):
        """
        Update fields of a record.

        ### Args

        - **record_id** (str): The id of the record
        - **data** (dict): The fields to change
        - **files** (list): Files to upload, as ```(field, (filename,
            content, content_type))``` tuples
        - **expand** (str): The relations to expand in the answer
        - **fields** (str): The fields to return
        - **query** (dict): Further query parameters

        ### Returns

        - **Record**: The changed record

        """
        return self._write('PATCH', self._record_path(record_id), data, files, expand, fields, query)

    def delete(self, record_id, query=None):
        """
        Delete a record.

        ### Args

        - **record_id** (str): The id of the record
        - **query** (dict): Further query parameters

        ### Returns

        - **bool**: True once deleted

        ### Raises

        - **TokeoPocketBaseError**: With status 404 if the record is unknown

        """
        self.client.send('DELETE', self._record_path(record_id), params=query)
        return True
