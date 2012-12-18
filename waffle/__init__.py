from decimal import Decimal
from functools import wraps
import random

from django.conf import settings
from django.core.cache import cache
from django.db.models.signals import post_save, post_delete, m2m_changed
from django.core.exceptions import ImproperlyConfigured
from django.utils.importlib import import_module

from waffle.models import Flag, Sample, Switch
from waffle.signals import flag_evaluated, sample_evaluated, switch_evaluated


VERSION = (0, 8, 1, 3)
__version__ = '.'.join(map(str, VERSION))


CACHE_PREFIX = getattr(settings, 'WAFFLE_CACHE_PREFIX', u'waffle:')
FLAG_CACHE_KEY = CACHE_PREFIX + u'flag:{n}'
FLAGS_ALL_CACHE_KEY = CACHE_PREFIX + u'flags:all'
FLAG_USERS_CACHE_KEY = CACHE_PREFIX + u'flag:{n}:users'
FLAG_GROUPS_CACHE_KEY = CACHE_PREFIX + u'flag:{n}:groups'
SAMPLE_CACHE_KEY = CACHE_PREFIX + u'sample:{n}'
SAMPLES_ALL_CACHE_KEY = CACHE_PREFIX + u'samples:all'
SWITCH_CACHE_KEY = CACHE_PREFIX + u'switch:{n}'
SWITCHES_ALL_CACHE_KEY = CACHE_PREFIX + u'switches:all'
COOKIE_NAME = getattr(settings, 'WAFFLE_COOKIE', 'dwf_%s')
TEST_COOKIE_NAME = getattr(settings, 'WAFFLE_TESTING_COOKIE', 'dwft_%s')
_FLAG_PERCENT_HANDLER = None


def _get_flag_percent_handler_func():
    global _FLAG_PERCENT_HANDLER
    if _FLAG_PERCENT_HANDLER:
        return _FLAG_PERCENT_HANDLER

    handler = getattr(settings, 'WAFFLE_FLAG_PERCENT_HANDLER', None)
    if not handler:
        _FLAG_PERCENT_HANDLER = lambda *args: None
        return _FLAG_PERCENT_HANDLER

    if callable(handler):
        return handler

    import_path = handler
    try:
        dot = import_path.rindex('.')
    except ValueError:
        raise ImproperlyConfigured("%s isn't a module path." % import_path)

    module, funcname = import_path[:dot], import_path[dot + 1:]
    try:
        mod = import_module(module)
    except ImportError, e:
        raise ImproperlyConfigured('Error importing module %s: "%s"' % (module, e))
    try:
        func = getattr(mod, funcname)
    except AttributeError:
        raise ImproperlyConfigured('Module "%s" does not define a "%s" function.' % (module, funcname))
    _FLAG_PERCENT_HANDLER = func
    return func


class DoesNotExist(object):
    """The record does not exist."""
    @property
    def active(self):
        return getattr(settings, 'WAFFLE_SWITCH_DEFAULT', False)


def send_signal(signal, argnames=None, resultname=None):
    def _my_decorator(f):
        @wraps(f)
        def _wrapper(*args, **kwargs):
            result = f(*args, **kwargs)
            sigargs = dict(zip(argnames, args))
            sigargs[resultname] = result
            signal.send_robust(sender=f, **sigargs)
            return result
        return _wrapper
    return _my_decorator


def set_flag(request, flag_name, active=True, session_only=False):
    """Set a flag value on a request object."""
    if not hasattr(request, 'waffles'):
        request.waffles = {}
    request.waffles[flag_name] = [active, session_only]


@send_signal(flag_evaluated, argnames=['request', 'name'], resultname='active')
def flag_is_active(request, flag_name):
    flag = cache.get(FLAG_CACHE_KEY.format(n=flag_name))
    if flag is None:
        try:
            flag = Flag.objects.get(name=flag_name)
            cache_flag(instance=flag)
        except Flag.DoesNotExist:
            return getattr(settings, 'WAFFLE_FLAG_DEFAULT', False)

    if getattr(settings, 'WAFFLE_OVERRIDE', False):
        if flag_name in request.GET:
            return request.GET[flag_name] == '1'

    if flag.everyone:
        return True
    elif flag.everyone is False:
        return False

    if flag.testing:  # Testing mode is on.
        tc = TEST_COOKIE_NAME % flag_name
        if tc in request.GET:
            on = request.GET[tc] == '1'
            if not hasattr(request, 'waffle_tests'):
                request.waffle_tests = {}
            request.waffle_tests[flag_name] = on
            return on
        if tc in request.COOKIES:
            return request.COOKIES[tc] == 'True'

    user = request.user

    if flag.authenticated and user.is_authenticated():
        return True

    if flag.staff and user.is_staff:
        return True

    if flag.superusers and user.is_superuser:
        return True

    if flag.languages:
        languages = flag.languages.split(',')
        if (hasattr(request, 'LANGUAGE_CODE') and
                request.LANGUAGE_CODE in languages):
            return True

    flag_users = cache.get(FLAG_USERS_CACHE_KEY.format(n=flag.name))
    if flag_users is None:
        flag_users = flag.users.all()
        cache_flag(instance=flag)
    if user in flag_users:
        return True

    flag_groups = cache.get(FLAG_GROUPS_CACHE_KEY.format(n=flag.name))
    if flag_groups is None:
        flag_groups = flag.groups.all()
        cache_flag(instance=flag)
    for group in flag_groups:
        if group in user.groups.all():
            return True

    if flag.percent > 0:
        handler = _get_flag_percent_handler_func()
        result = handler(request, flag.name, flag.percent, flag.rollout)
        if result is not None:
            return result

        if getattr(settings, 'WAFFLE_PERCENT_ON_USERID', False) and user.is_authenticated():
            if hasattr(user, 'id') and isinstance(user.id, (int, long)):
                return ((user.id % 1000) / 10.0) <= flag.percent

        if not hasattr(request, 'waffles'):
            request.waffles = {}
        elif flag_name in request.waffles:
            return request.waffles[flag_name][0]

        cookie = COOKIE_NAME % flag_name
        if cookie in request.COOKIES:
            flag_active = (request.COOKIES[cookie] == 'True')
            set_flag(request, flag_name, flag_active, flag.rollout)
            return flag_active

        if Decimal(str(random.uniform(0, 100))) <= flag.percent:
            set_flag(request, flag_name, True, flag.rollout)
            return True
        set_flag(request, flag_name, False, flag.rollout)

    return False


@send_signal(switch_evaluated, argnames=['name'], resultname='active')
def switch_is_active(switch_name):
    switch = cache.get(SWITCH_CACHE_KEY.format(n=switch_name))
    if switch is None:
        try:
            switch = Switch.objects.get(name=switch_name)
            cache_switch(instance=switch)
        except Switch.DoesNotExist:
            switch = DoesNotExist()
            switch.name = switch_name
            cache_switch(instance=switch)
    return switch.active


@send_signal(sample_evaluated, argnames=['name'], resultname='active')
def sample_is_active(sample_name):
    sample = cache.get(SAMPLE_CACHE_KEY.format(n=sample_name))
    if sample is None:
        try:
            sample = Sample.objects.get(name=sample_name)
            cache_sample(instance=sample)
        except Sample.DoesNotExist:
            return getattr(settings, 'WAFFLE_SAMPLE_DEFAULT', False)

    return Decimal(str(random.uniform(0, 100))) <= sample.percent


def cache_flag(**kwargs):
    action = kwargs.get('action', None)
    # action is included for m2m_changed signal. Only cache on the post_*.
    if not action or action in ['post_add', 'post_remove', 'post_clear']:
        f = kwargs.get('instance')
        cache.add(FLAG_CACHE_KEY.format(n=f.name), f)
        cache.add(FLAG_USERS_CACHE_KEY.format(n=f.name), f.users.all())
        cache.add(FLAG_GROUPS_CACHE_KEY.format(n=f.name), f.groups.all())


def uncache_flag(**kwargs):
    flag = kwargs.get('instance')
    data = {
        FLAG_CACHE_KEY.format(n=flag.name): None,
        FLAG_USERS_CACHE_KEY.format(n=flag.name): None,
        FLAG_GROUPS_CACHE_KEY.format(n=flag.name): None,
        FLAGS_ALL_CACHE_KEY: None
    }
    cache.set_many(data, 5)

post_save.connect(uncache_flag, sender=Flag, dispatch_uid='save_flag')
post_delete.connect(uncache_flag, sender=Flag, dispatch_uid='delete_flag')
m2m_changed.connect(uncache_flag, sender=Flag.users.through,
                    dispatch_uid='m2m_flag_users')
m2m_changed.connect(uncache_flag, sender=Flag.groups.through,
                    dispatch_uid='m2m_flag_groups')


def cache_sample(**kwargs):
    sample = kwargs.get('instance')
    cache.add(SAMPLE_CACHE_KEY.format(n=sample.name), sample)


def uncache_sample(**kwargs):
    sample = kwargs.get('instance')
    cache.set(SAMPLE_CACHE_KEY.format(n=sample.name), None, 5)
    cache.set(SAMPLES_ALL_CACHE_KEY, None, 5)

post_save.connect(uncache_sample, sender=Sample, dispatch_uid='save_sample')
post_delete.connect(uncache_sample, sender=Sample,
                    dispatch_uid='delete_sample')


def cache_switch(**kwargs):
    switch = kwargs.get('instance')
    cache.add(SWITCH_CACHE_KEY.format(n=switch.name), switch)


def uncache_switch(**kwargs):
    switch = kwargs.get('instance')
    cache.set(SWITCH_CACHE_KEY.format(n=switch.name), None, 5)
    cache.set(SWITCHES_ALL_CACHE_KEY, None, 5)

post_delete.connect(uncache_switch, sender=Switch,
                    dispatch_uid='delete_switch')
post_save.connect(uncache_switch, sender=Switch, dispatch_uid='save_switch')
