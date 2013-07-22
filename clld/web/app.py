"""
Common functionality of CLLD Apps is cobbled together here.
"""
from functools import partial
from collections import OrderedDict
import re
import importlib
from hashlib import md5

from sqlalchemy import engine_from_config
from sqlalchemy.orm import joinedload_all, joinedload
from sqlalchemy.orm.exc import NoResultFound

from path import path
import transaction
from webob.request import Request as WebobRequest
from zope.interface import implementer, implementedBy
from pyramid.httpexceptions import HTTPNotFound
from pyramid import events
from pyramid.request import Request, reify
from pyramid.interfaces import IRoutesMapper
from pyramid.asset import abspath_from_asset_spec
from purl import URL

import clld
from clld.config import get_config
from clld.db.meta import DBSession, Base
from clld.db.models import common
from clld import Resource, RESOURCES
from clld import interfaces
from clld.web.adapters import get_adapters
from clld.web.adapters import excel
from clld.web.views import index_view, resource_view, _raise, _ping, js, unapi
from clld.web.views.olac import olac, OlacConfig
from clld.web.views.sitemap import robots, sitemapindex, sitemap
from clld.web.subscribers import add_renderer_globals, add_localizer, init_map
from clld.web.datatables.base import DataTable
from clld.web import datatables
from clld.web.maps import Map, ParameterMap, LanguageMap
from clld.web.icon import ICONS, MapMarker
from clld.web import assets


class ClldRequest(Request):
    """Custom Request class
    """
    @reify
    def purl(self):
        """For more convenient URL manipulations, we provide a PURL-variant of the current
        request's URL.
        """
        return URL(self.url)

    @property
    def db(self):
        """We make the db session available as request attribute, so we do not have to
        import it in templates.
        """
        return DBSession

    @reify
    def dataset(self):
        return self.db.query(common.Dataset).first()

    def _route(self, obj, rsc, **kw):
        if rsc is None:
            for _rsc in RESOURCES:
                if _rsc.interface.providedBy(obj):
                    rsc = _rsc
                    break
            assert rsc

        route = rsc.name
        if 'ext' in kw:
            route += '_alt'

        # if rsc is passed explicitely, we allow the object id to be passed in as obj,
        # to make it possible to create resource URLs without having the "real" object.
        kw.setdefault('id', getattr(obj, 'id', obj))
        return route, kw

    def ctx_for_url(self, url):
        mapper = self.registry.getUtility(IRoutesMapper)
        _path = URL(url).path()
        info = mapper(WebobRequest({'PATH_INFO': _path}))
        if not info['route']:
            # FIXME: hack to cater to deployments under a path prefix
            info = mapper(WebobRequest({'PATH_INFO': re.sub('^\/[a-z]+', '', _path)}))
        if info['route'] and info['match']:
            for rsc in RESOURCES:
                if rsc.name == info['route'].name:
                    return rsc.model.get(info['match']['id'], default=None)

    def resource_url(self, obj, rsc=None, **kw):
        route, kw = self._route(obj, rsc, **kw)
        return self.route_url(route, **kw)

    def resource_path(self, obj, rsc=None, **kw):
        route, kw = self._route(obj, rsc, **kw)
        return self.route_path(route, **kw)


def menu_item(resource, ctx, req, label=None):
    """
    :return: A pair (URL, label) to create a menu item.
    """
    return req.route_url(resource), label or req.translate(resource.capitalize())


@implementer(interfaces.ICtxFactoryQuery)
class CtxFactoryQuery(object):
    def refined_query(self, query, model, req):
        """Derived classes may override this method to add model-specific query
        refinements of their own.
        """
        return query

    def __call__(self, model, req):
        query = req.db.query(model).filter(model.id == req.matchdict['id'])
        custom_query = self.refined_query(query, model, req)

        if query == custom_query:
            # no customizations done, apply the defaults
            if model == common.Contribution:
                query = query.options(
                    joinedload_all(
                        common.Contribution.valuesets,
                        common.ValueSet.parameter,
                    ),
                    joinedload_all(
                        common.Contribution.valuesets,
                        common.ValueSet.values,
                        common.Value.domainelement),
                    joinedload_all(
                        common.Contribution.references,
                        common.ContributionReference.source),
                    joinedload(common.Contribution.data),
                )
            if model == common.ValueSet:
                query = query.options(
                    joinedload(common.ValueSet.values),
                    joinedload(common.ValueSet.parameter),
                    joinedload(common.ValueSet.language),
                )
        else:
            query = custom_query  # pragma: no cover

        return query.one()


def ctx_factory(model, type_, req):
    """The context of a request is either a single model instance or an instance of
    DataTable incorporating all information to retrieve an appropriately filtered list
    of model instances.
    """
    if type_ == 'index':
        datatable = req.registry.getUtility(
            interfaces.IDataTable, name=req.matched_route.name)
        return datatable(req, model)

    try:
        if model == common.Dataset:
            ctx = req.db.query(model).one()
        else:
            ctx = req.registry.getUtility(interfaces.ICtxFactoryQuery)(model, req)
        ctx.metadata = get_adapters(interfaces.IMetadata, ctx, req)
        return ctx
    except NoResultFound:
        raise HTTPNotFound()


def register_cls(interface, config, route, cls):
    config.registry.registerUtility(cls, provided=interface, name=route)
    if not route.endswith('_alt'):
        config.registry.registerUtility(cls, provided=interface, name=route + '_alt')


def register_app(config, pkg=None):
    """This hook can be used by apps to have some conventional locations for resources
    within the package be exploited automatically to update the registry.
    """
    if pkg is None:
        config.add_translation_dirs('clld:locale')
        menuitems = OrderedDict(dataset=partial(menu_item, 'dataset', label='home'))
        config.registry.registerUtility(menuitems, interfaces.IMenuItems)
        return

    if not hasattr(pkg, '__file__'):
        pkg = __import__(pkg)
    name = pkg.__name__
    pkg_dir = path(pkg.__file__).dirname().abspath()

    if pkg_dir.joinpath('assets.py').exists():
        importlib.import_module('%s.assets' % name)

    if pkg_dir.joinpath('util.py').exists():
        u = importlib.import_module('%s.util' % name)

        def add_util(event):
            event['u'] = u  # pragma: no cover

        config.add_subscriber(add_util, events.BeforeRender)

    if pkg_dir.joinpath('locale').exists():
        config.add_translation_dirs('%s:locale' % name)
        config.add_translation_dirs('clld:locale')

    if pkg_dir.joinpath('appconf.ini').exists():
        cfg = get_config(pkg_dir.joinpath('appconf.ini'))
        if 'mako.directories_list' in cfg:
            cfg['mako.directories'] = cfg['mako.directories_list']  # pragma: no cover
        config.add_settings(cfg)

    config.add_static_view('static', '%s:static' % name, cache_max_age=3600)
    if pkg_dir.joinpath('views.py').exists() or pkg_dir.joinpath('views').exists():
        config.scan('%s.views' % name)  # pragma: no cover

    menuitems = OrderedDict(dataset=partial(menu_item, 'dataset', label='home'))
    for plural in config.registry.settings.get(
        'clld.menuitems_list',
        ['contributions', 'parameters', 'languages', 'contributors']
    ):
        menuitems[plural] = partial(menu_item, plural)
    config.registry.registerUtility(menuitems, interfaces.IMenuItems)


def includeme(config):
    config.set_request_factory(ClldRequest)

    config.registry.registerUtility(CtxFactoryQuery(), interfaces.ICtxFactoryQuery)
    config.registry.registerUtility(OlacConfig(), interfaces.IOlacConfig)

    # initialize the db connection
    engine = engine_from_config(config.registry.settings, 'sqlalchemy.')
    DBSession.configure(bind=engine)
    Base.metadata.bind = engine

    config.add_settings({'pyramid.default_locale_name': 'en'})
    if 'clld.favicon' not in config.registry.settings:
        config.add_settings({'clld.favicon': 'clld:web/static/images/favicon.ico'})
    fh = md5()
    fh.update(
        open(abspath_from_asset_spec(config.registry.settings['clld.favicon'])).read())
    config.add_settings({'clld.favicon_hash': fh.hexdigest()})

    # event subscribers:
    config.add_subscriber(add_localizer, events.NewRequest)
    config.add_subscriber(add_renderer_globals, events.BeforeRender)
    config.add_subscriber(init_map, events.ContextFound)

    config.add_static_view(name='clld-static', path='clld:web/static')

    #
    # make it easy to register custom functionality
    #
    config.add_directive(
        'register_datatable', partial(register_cls, interfaces.IDataTable))
    config.add_directive('register_map', partial(register_cls, interfaces.IMap))

    def add_menu_item(config, name, factory):
        """
        :param factory: a callable that accepts the two parameters (ctx, req) and returns\
        a pair (url, label) to use for the menu link.
        """
        # we retrieve the currently registered menuitems
        menuitems = config.registry.getUtility(interfaces.IMenuItems)
        # add one
        menuitems[name] = factory
        # and re-register.
        config.registry.registerUtility(menuitems, interfaces.IMenuItems)

    config.add_directive('add_menu_item', add_menu_item)

    # TODO:
    # register utility route_pattern_map to allow for custom route patterns!
    # maps route names to route patterns

    def register_resource(config, name, model, interface, with_index=False):
        RESOURCES.append(Resource(name, model, interface, with_index=with_index))
        config.register_adapter(excel.ExcelAdapter, interface)
        config.add_route_and_view(
            name,
            '/%ss/{id:[^/\.]+}' % name,
            resource_view,
            factory=partial(ctx_factory, model, 'rsc'))
        if with_index:
            config.add_route_and_view(
                name + 's',
                '/%ss' % name,
                index_view,
                factory=partial(ctx_factory, model, 'index'))

    config.add_directive('register_resource', register_resource)

    def register_adapter(config, cls, from_, to_=None, name=None):
        to_ = to_ or list(implementedBy(cls))[0]
        name = name or cls.mimetype
        config.registry.registerAdapter(cls, (from_,), to_, name=name)

    config.add_directive('register_adapter', register_adapter)

    def add_route_and_view(config, route_name, route_pattern, view, **kw):
        route_patterns = config.registry.settings.get('route_patterns', {})
        route_pattern = route_patterns.get(route_name, route_pattern)
        alt_route_pattern = kw.pop('alt_route_pattern', route_pattern + '.{ext}')
        route_kw = {}
        factory = kw.pop('factory', None)
        if factory:
            route_kw['factory'] = factory
        config.add_route(route_name, route_pattern, **route_kw)
        config.add_view(view, route_name=route_name, **kw)

        config.add_route(route_name + '_alt', alt_route_pattern, **route_kw)
        config.add_view(view, route_name=route_name + '_alt', **kw)

    config.add_directive('add_route_and_view', add_route_and_view)

    config.add_directive('register_app', register_app)

    #
    # routes and views
    #
    config.add_route_and_view('legal', '/legal', lambda r: {}, renderer='legal.mako')
    config.add_route_and_view('_js', '/_js', js, http_cache=3600)

    # add some maintenance hatches
    config.add_route_and_view('_raise', '/_raise', _raise)
    config.add_route_and_view('_ping', '/_ping', _ping, renderer='json')

    # sitemap support:
    config.add_route_and_view('robots', '/robots.txt', robots)
    config.add_route_and_view('sitemapindex', '/sitemap.xml', sitemapindex)
    config.add_route_and_view('sitemap', '/sitemap.{rsc}.{n}.xml', sitemap)

    config.add_route_and_view('unapi', '/unapi', unapi)
    config.add_route_and_view('olac', '/olac', olac, renderer='olac.mako')

    for rsc in RESOURCES:
        name, model = rsc.name, rsc.model
        plural = name + 's'
        if rsc.with_index:
            factory = partial(ctx_factory, model, 'index')

            # lookup route pattern!

            config.add_route_and_view(plural, '/%s' % plural, index_view, factory=factory)
            config.register_datatable(
                plural, getattr(datatables, plural.capitalize(), DataTable))
            config.register_adapter(getattr(excel, plural.capitalize(), excel.ExcelAdapter), rsc.interface)

        kw = dict(factory=partial(ctx_factory, model, 'rsc'))
        if model == common.Dataset:
            pattern = '/'
            kw['alt_route_pattern'] = '/void.{ext}'
        else:
            pattern = '/%s/{id:[^/\.]+}' % plural

        # lookup route pattern!

        config.add_route_and_view(name, pattern, resource_view, **kw)

    # maps
    config.register_map('languages', Map)
    config.register_map('language', LanguageMap)
    config.register_map('parameter', ParameterMap)

    config.include('clld.web.adapters')

    for icon in ICONS:
        config.registry.registerUtility(icon, interfaces.IIcon, name=icon.name)
    config.registry.registerUtility(MapMarker(), interfaces.IMapMarker)
