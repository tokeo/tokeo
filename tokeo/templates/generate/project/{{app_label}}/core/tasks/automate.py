from spiral.core import tasks


def count_words(app, connection, verbose=False):
    app.log.info('Automation count_words called')
    result = connection.run('uname -mrs', hide=not verbose, warn=False)
    url = f'https://goole.com/q="{result.stdout}"'.replace('\n', '').replace('\r', '')
    app.log.info(f'Run actor from automation with url: {url}')
    tasks.actors.count_words.send(url)
    return True


def uname(app, connection, verbose=False, flags=None):
    app.log.info('Automation uname called')
    return connection.run(f'uname {" ".join(flags)}', hide=not verbose, warn=False)
