from django.shortcuts import render_to_response
from django.views.decorators.cache import never_cache

from waffle import (flag_is_active, sample_is_active, get_all_flag_names,
                    get_all_switch_tuples, get_all_sample_names)


@never_cache
def wafflejs(request):
    flag_values = [(f, flag_is_active(request, f)) for f in get_all_flag_names()]
    sample_values = [(s, sample_is_active(s)) for s in get_all_sample_names()]

    return render_to_response('waffle/waffle.js', {'flags': flag_values,
                                                   'switches': get_all_switch_tuples(),
                                                   'samples': sample_values},
                              mimetype='application/x-javascript')
