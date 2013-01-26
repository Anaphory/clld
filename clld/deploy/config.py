from path import path


def repos(name):
    return 'git://github.com/clld/%s.git' % name


class App(object):
    def __init__(self, name, port, **kw):
        self.name = name
        self.port = port

        for k, v in kw.items():
            setattr(self, k, v)

    @property
    def venv(self):
        return path('/usr/local/venvs').joinpath(self.name)

    @property
    def home(self):
        return path('/home').joinpath(self.name)

    @property
    def config(self):
        return self.home.joinpath('config.ini')

    @property
    def logs(self):
        return path('/var/log').joinpath(self.name)

    def bin(self, command):
        return self.venv.joinpath('bin', command)

    @property
    def repos(self):
        return repos(self.name)

    @property
    def upstart(self):
        return path('/etc/init').joinpath('%s.conf' % self.name)

    @property
    def nginx_location(self):
        return path('/etc/nginx/locations.d').joinpath('%s.conf' % self.name)

    @property
    def nginx_site(self):
        return path('/etc/nginx/sites-enabled').joinpath(self.name)

    @property
    def sqlalchemy_url(self):
        return 'postgresql://{0}@/{0}'.format(self.name)


APPS = dict((app.name, app) for app in [
    App('wold2', 8888, domain='wold.livingsources.org'),
    App('wals3', 8887, domain='wals.info'),
])

ERROR_EMAIL = 'robert_forkel@eva.mpg.de'
