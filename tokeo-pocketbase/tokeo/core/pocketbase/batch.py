"""
Record changes sent in one transaction, all or nothing, by the batch api of
PocketBase; it must be switched on in the settings of PocketBase.

"""

from .record import Record
from .filter import path_segment


class Batch:
    """
    Record changes sent together in one transaction.

    ### Args

    - **client** (PocketBase): The client that sends the batch

    ### Example

    ```python
    batch = pb.batch()
    batch.create('posts', {'title': 'A'})
    batch.update('posts', post_id, {'views': 4})
    batch.delete('posts', old_id)
    created, updated, deleted = batch.send()
    ```

    ### Notes

    : The batch api must be enabled in the settings of PocketBase

    : Either all changes are done or none, a failed one raises with the
        failed request in ```data['requests']```

    """

    def __init__(self, client):
        #: The client that sends the batch
        self.client = client
        #: The requests of the batch
        self.requests = []

    def _add(self, method, collection, record_id, data):
        # one request of the batch
        url = f'/api/collections/{path_segment(collection)}/records'
        if record_id is not None:
            url = f'{url}/{path_segment(record_id)}'
        request = {'method': method, 'url': url}
        if data is not None:
            request['body'] = data
        self.requests.append(request)
        return self

    def create(self, collection, data):
        """Add the creation of a record, returns the batch."""
        return self._add('POST', collection, None, data)

    def update(self, collection, record_id, data):
        """Add the change of a record, returns the batch."""
        return self._add('PATCH', collection, record_id, data)

    def upsert(self, collection, data):
        """Add the creation or change of a record by its id, returns the batch."""
        return self._add('PUT', collection, None, data)

    def delete(self, collection, record_id):
        """Add the deletion of a record, returns the batch."""
        return self._add('DELETE', collection, record_id, None)

    def send(self):
        """
        Send all changes in one transaction.

        ### Returns

        - **list**: The record of each change in order, None for a deletion

        ### Raises

        - **TokeoPocketBaseError**: If the batch is refused or a change fails,
            nothing is changed then

        """
        answers = self.client.send('POST', '/api/batch', json={'requests': self.requests})
        return [Record(answer['body']) if answer.get('body') is not None else None for answer in answers]
