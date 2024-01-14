import dramatiq
import requests
import subprocess


@dramatiq.actor(queue_name='count_words')
def count_words(url):
    response = requests.get(url)
    count = len(response.text.split(' '))
    print(f'There are {count} words at {url!r}.')
