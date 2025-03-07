import dramatiq
import requests
from tokeo.ext.appshare import app  # noqa: F401


@dramatiq.actor(queue_name='count_words')
def count_words(url):
    response = requests.get(url)
    count = len(response.text.split(' '))
    app.log.info(f'There are {count} words at {url!r}.')
