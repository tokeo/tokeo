from tokeo.ext.appshare import app
from {{ app_label }}.core import tasks


def count_word_timer(url=''):
    app.log.info('Timer start with url: ' + url)
    tasks.actors.count_words.send(url)
