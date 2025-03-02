def uname(app, connection, verbose=False, flags=None):
    app.log.info('Automation uname called')
    return connection.run(f'uname {" ".join(flags)}', hide=not verbose, warn=False)
