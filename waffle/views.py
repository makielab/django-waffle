from django.shortcuts import render_to_response
from django.views.decorators.cache import never_cache

from waffle import (flag_is_active, sample_is_active, get_all_flags,
                    get_all_switches, get_all_samples)


@never_cache
def wafflejs(request):
    flag_values = [(f, flag_is_active(request, f)) for f in get_all_flags()]
    sample_values = [(s, sample_is_active(s)) for s in get_all_samples()]

    return render_to_response('waffle/waffle.js', {'flags': flag_values,
                                                   'switches': get_all_switches(),
                                                   'samples': sample_values},
                              mimetype='application/x-javascript')
