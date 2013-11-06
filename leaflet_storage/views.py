# -*- coding:utf-8 -*-

import os

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout as do_logout
from django.contrib.auth.models import User
from django.contrib.gis.geos import GEOSGeometry
from django.core.signing import Signer, BadSignature
from django.core.urlresolvers import reverse_lazy
from django.db import transaction
from django.http import (HttpResponse, HttpResponseForbidden, Http404,
                         HttpResponseRedirect, CompatibleStreamingHttpResponse)
from django.shortcuts import get_object_or_404
from django.template import RequestContext
from django.template.loader import render_to_string
from django.utils import simplejson
from django.utils.translation import ugettext as _
from django.views.generic import View
from django.views.generic import DetailView
from django.views.generic.detail import BaseDetailView
from django.views.generic.list import BaseListView, ListView
from django.views.generic.base import TemplateView, RedirectView
from django.views.generic.edit import CreateView, UpdateView, FormView, DeleteView
from django.utils.http import http_date

from vectorformats.formats import django, geojson

from .models import (Map, Marker, DataLayer, Polyline, TileLayer,
                     Polygon, Pictogram, Licence)
from .utils import get_uri_template, smart_decode
from .forms import (QuickMapCreateForm, UpdateMapExtentForm, DataLayerForm,
                    UploadDataForm, UpdateMapPermissionsForm, MapSettingsForm,
                    MarkerForm, PolygonForm, PolylineForm, AnonymousMapPermissionsForm,
                    DownloadDataForm, UpdateMapTileLayerForm, DEFAULT_LATITUDE, 
                    DEFAULT_LONGITUDE)


# ############## #
#     Utils      #
# ############## #

def _urls_for_js(urls=None):
    """
    Return templated URLs prepared for javascript.
    """
    if urls is None:
        # prevent circular import
        from .urls import urlpatterns
        urls = [url.name for url in urlpatterns if getattr(url, 'name', None)]
    return dict(zip(urls, [get_uri_template(url) for url in urls]))


def render_to_json(templates, response_kwargs, context, request):
    """
    Generate a JSON HttpResponse with rendered template HTML.
    """
    html = render_to_string(
        templates,
        response_kwargs,
        RequestContext(request, context)
    )
    _json = simplejson.dumps({
        "html": html
    })
    return HttpResponse(_json)


def simple_json_response(**kwargs):
    return HttpResponse(simplejson.dumps(kwargs))


# ############## #
#      Map       #
# ############## #

class GeoJSONMixin(object):

    geojson_fields = ['name', 'datalayer_id', 'options', 'icon']

    def geojson(self, context):
        qs = self.get_queryset()
        djf = django.Django(geodjango="latlng", properties=self.geojson_fields)
        geoj = geojson.GeoJSON()
        return geoj.encode(djf.decode(qs), to_string=False)


class FormLessEditMixin(object):

    def form_invalid(self, form):
        return simple_json_response(errors=form.errors, infos=_("An error occured."))



class MapDetailMixin(object):

    model = Map

    def get_context_data(self, **kwargs):
        context = super(MapDetailMixin, self).get_context_data(**kwargs)
        properties = {}
        properties['datalayers'] = self.get_datalayers()
        properties['urls'] = _urls_for_js()
        properties['tilelayers'] = self.get_tilelayers()
        # properties['name'] = self.object.name
        # properties['description'] = self.object.description
        if self.get_short_url():
            properties['shortUrl'] = self.get_short_url()

        if settings.USE_I18N:
            locale = settings.LANGUAGE_CODE
            # Check attr in case the middleware is not active
            if hasattr(self.request, "LANGUAGE_CODE"):
                locale = self.request.LANGUAGE_CODE
            properties['locale'] = locale
        # Precedence to GET param
        # allow_edit = self.get_int_from_request("allowEdit", allow_edit) # TODO mv to js
        properties['allowEdit'] = self.is_edit_allowed()
        # for name, label, default in MapSettingsForm.SETTINGS:
        #     value = self.get_int_from_request(name, self.object.settings.get(name, default))
        #     try:
        #         value = int(value)
        #     except ValueError:
        #         value = default
        #     properties[name] = value
        properties["default_iconUrl"] = "%sstorage/src/img/marker.png" % settings.STATIC_URL
        # properties['center'] = simplejson.loads(self.object.center.geojson)
        properties['storage_id'] = self.get_storage_id()
        # properties['zoom'] = self.object.zoom
        properties['licences'] = dict((l.name, l.json) for l in Licence.objects.all())
        # properties['licence'] = self.object.licence.json
        # if properties['locateOnLoad']:
        #     properties['locate'] = {
        #         'setView': True,
        #         'enableHighAccuracy': True,
        #         'timeout': 3000
        #     }
        map_settings = self.get_geojson()
        if not "properties" in map_settings:
            map_settings['properties'] = {}
        map_settings['properties'].update(properties)
        context['map_settings'] = simplejson.dumps(map_settings)
        return context

    def get_tilelayers(self):
        return TileLayer.get_list(selected=TileLayer.get_default())

    def get_datalayers(self):
        return []

    def is_edit_allowed(self):
        return True

    def get_storage_id(self):
        return None

    def get_geojson(self):
        return {
            "geometry": {
                "coordinates": [DEFAULT_LONGITUDE, DEFAULT_LATITUDE],
                "type": "Point"
            }
        }

    def get_short_url(self):
        return None


class MapView(MapDetailMixin, DetailView):

    def get_datalayers(self):
        datalayers = DataLayer.objects.filter(map=self.object)  # TODO manage state
        return [l.metadata for l in datalayers]

    def get_tilelayers(self):
        return TileLayer.get_list(selected=self.object.get_tilelayer())

    def is_edit_allowed(self):
        if self.request.user.is_authenticated():
            allow_edit = self.object.can_edit(self.request.user, self.request)
        else:
            # Default to True: display buttons for anonymous, they can
            # login from action process
            allow_edit = True
        return allow_edit

    def get_storage_id(self):
        return self.object.pk

    def get_short_url(self):
        shortUrl = None
        if hasattr(settings, 'SHORT_SITE_URL'):
            short_url_name = getattr(settings, 'MAP_SHORT_URL_NAME', 'map_short_url')
            short_path = reverse_lazy(short_url_name, kwargs={'pk': self.object.pk})
            shortUrl = "%s%s" % (settings.SHORT_SITE_URL, short_path)
        return shortUrl

    def get_geojson(self):
        return self.object.geojson


class MapNew(MapDetailMixin, TemplateView):
    template_name = "leaflet_storage/map_detail.html"


class MapCreate(FormLessEditMixin, CreateView):
    model = Map
    form_class = MapSettingsForm

    def form_valid(self, form):
        if self.request.user.is_authenticated():
            form.instance.owner = self.request.user
        self.object = form.save()
        if not self.request.user.is_authenticated():
            anonymous_url = "%s%s" % (
                settings.SITE_URL,
                self.object.get_anonymous_edit_url()
            )
            msg = _(
                "Your map has been created! If you want to edit this map from "
                "another computer, please use this link: %(anonymous_url)s"
                % {"anonymous_url": anonymous_url}
            )
        else:
            msg = _("Congratulations, your map has been created!")
        response = simple_json_response(
            pk=self.object.pk,
            url=self.object.get_absolute_url(),
            info=msg
        )
        if not self.request.user.is_authenticated():
            key, value = self.object.signed_cookie_elements
            response.set_signed_cookie(key, value)
        return response


class MapUpdate(FormLessEditMixin, UpdateView):
    model = Map
    form_class = MapSettingsForm
    pk_url_kwarg = 'map_id'

    def form_valid(self, form):
        self.object.settings = form.cleaned_data["settings"]
        self.object.save()
        return simple_json_response(
            pk=self.object.pk,
            url=self.object.get_absolute_url(),
            info=_("Map has been updated!")
        )


class MapInfos(DetailView):
    model = Map
    template_name = "leaflet_storage/map_infos.html"
    pk_url_kwarg = 'map_id'

    def render_to_response(self, context, **response_kwargs):
        return render_to_json(self.get_template_names(), response_kwargs, context, self.request)


class QuickMapCreate(CreateView):
    model = Map
    form_class = QuickMapCreateForm

    def form_valid(self, form):
        """
        Provide default values, to keep form simple.
        """
        if self.request.user.is_authenticated():
            form.instance.owner = self.request.user
        form.instance.tilelayer = TileLayer.get_default()
        self.object = form.save()
        DataLayer.create_default(self.object)
        response = simple_json_response(redirect=self.get_success_url())
        if not self.request.user.is_authenticated():
            key, value = self.object.signed_cookie_elements
            response.set_signed_cookie(key, value)
            anonymous_url = "%s%s" % (
                settings.SITE_URL,
                self.object.get_anonymous_edit_url()
            )
            msg = _(
                "Your map has been created! If you want to edit this map from "
                "another computer, please use this link: %(anonymous_url)s"
                % {"anonymous_url": anonymous_url}
            )
        else:
            msg = _("Congratulations, your map has been created! To start editing, click on the pen icon.")
        messages.info(self.request, msg)
        return response

    def render_to_response(self, context, **response_kwargs):
        return render_to_json(self.get_template_names(), response_kwargs, context, self.request)

    def get_context_data(self, **kwargs):
        kwargs.update({
            'action_url': reverse_lazy('map_add')
        })
        return super(QuickMapCreate, self).get_context_data(**kwargs)


# TODO: factorize with QuickCreate!
class QuickMapUpdate(UpdateView):
    model = Map
    form_class = QuickMapCreateForm
    pk_url_kwarg = 'map_id'

    def form_valid(self, form):
        self.object = form.save()
        return simple_json_response(redirect=self.get_success_url())

    def render_to_response(self, context, **response_kwargs):
        return render_to_json(self.get_template_names(), response_kwargs, context, self.request)

    def get_context_data(self, **kwargs):
        kwargs.update({
            'action_url': reverse_lazy('map_update', args=[self.object.pk]),
            'delete_url': reverse_lazy('map_delete', args=[self.object.pk]),
            'clone_url': reverse_lazy('map_clone', args=[self.object.pk]),
        })
        return super(QuickMapUpdate, self).get_context_data(**kwargs)


class UpdateMapExtent(UpdateView):
    model = Map
    form_class = UpdateMapExtentForm
    pk_url_kwarg = 'map_id'

    def form_invalid(self, form):
        return simple_json_response(info=form.errors)

    def form_valid(self, form):
        self.object = form.save()
        return simple_json_response(info=_("Zoom and center updated with success!"))


class UpdateMapTileLayer(UpdateView):
    model = Map
    form_class = UpdateMapTileLayerForm
    pk_url_kwarg = 'map_id'

    def form_invalid(self, form):
        return simple_json_response(info=form.errors)

    def form_valid(self, form):
        self.object = form.save()
        return simple_json_response(info=_("Map background updated with success!"))


class UpdateMapPermissions(UpdateView):
    template_name = "leaflet_storage/map_update_permissions.html"
    model = Map
    pk_url_kwarg = 'map_id'

    def get_form_class(self):
        if self.object.owner:
            return UpdateMapPermissionsForm
        else:
            return AnonymousMapPermissionsForm

    def get_form(self, form_class):
        form = super(UpdateMapPermissions, self).get_form(form_class)
        user = self.request.user
        if self.object.owner and not user == self.object.owner:
            del form.fields['edit_status']
        return form

    def form_valid(self, form):
        self.object = form.save()
        return simple_json_response(info=_("Map editors updated with success!"))

    def render_to_response(self, context, **response_kwargs):
        return render_to_json(self.get_template_names(), response_kwargs, context, self.request)


class UploadData(FormView):
    template_name = "leaflet_storage/upload_form.html"
    form_class = UploadDataForm
    pk_url_kwarg = 'map_id'

    def get_form(self, form_class):
        form = super(UploadData, self).get_form(form_class)
        map_inst = self.kwargs['map_inst']
        form.fields['datalayer'].queryset = DataLayer.objects.filter(map=map_inst)
        return form

    def get_context_data(self, **kwargs):
        kwargs.update({
            'action_url': reverse_lazy('upload_data', kwargs={'map_id': self.kwargs['map_id']})
        })
        return super(UploadData, self).get_context_data(**kwargs)

    @transaction.commit_manually
    def form_valid(self, form):
        FEATURE_TO_MODEL = {
            'Point': Marker,
            'LineString': Polyline,
            'Polygon': Polygon
        }
        # Use a tuple to add more sources possible
        # first item is field name
        FIELDS = [
            ('name', 'title', ),
            ('description', 'desc', 'text', ),
            ('color', 'hexcolor', )
        ]
        features = (form.cleaned_data.get('data_file')
                   or form.cleaned_data.get('data_url')
                   or form.cleaned_data.get('data_raw'))
        datalayer = form.cleaned_data.get('datalayer')
        counter = 0
        for feature in features:
            sid = transaction.savepoint()
            klass = FEATURE_TO_MODEL.get(feature.geometry['type'], None)
            if not klass:
                continue  # TODO notify user
            # Remove altitude, if there
            try:
                if feature.geometry['type'] == "LineString":
                    feature.geometry['coordinates'] = map(
                        lambda x: x[:2],
                        feature.geometry['coordinates']
                    )
                elif feature.geometry['type'] == "Point":
                    feature.geometry['coordinates'] = feature.geometry['coordinates'][:2]
                elif feature.geometry['type'] == "Polygon":
                    feature.geometry['coordinates'] = map(
                        lambda x: map(lambda y: y[:2], x),
                        feature.geometry['coordinates']
                    )
            except Exception:
                continue
            try:
                latlng = GEOSGeometry(simplejson.dumps(feature.geometry))
            except Exception:
                continue  # TODO notify user
            if latlng.empty:
                continue  # TODO notify user
            kwargs = {
                'latlng': latlng,
                'datalayer': datalayer,
                'name': datalayer.name  # Default
            }
            for field in FIELDS:
                if isinstance(field, tuple):
                    name = field[0]
                    candidates = field
                else:
                    name = field
                    candidates = [field]
                for candidate in candidates:
                    if candidate in feature.properties:
                        value = feature.properties[candidate]
                        if not value:
                            continue
                        value = smart_decode(value)
                        if name in klass._meta.get_all_field_names():
                            kwargs[name] = value
                        else:
                            # it's an option
                            if not "options" in kwargs:
                                kwargs['options'] = {}
                            kwargs['options'][name] = value
                        break
            try:
                klass.objects.create(**kwargs)
            except Exception:
                transaction.savepoint_rollback(sid)
                continue  # TODO notify user
            else:
                transaction.savepoint_commit(sid)
            counter += 1
        transaction.commit()
        if counter:
            kwargs = {
                'datalayer': datalayer.json,
                'info': _("%d features created!") % counter,
            }
        else:
            kwargs = {
                'error': _("No valid feature has been found :("),
            }
        return simple_json_response(**kwargs)

    def render_to_response(self, context, **response_kwargs):
        return render_to_json(self.get_template_names(), response_kwargs, context, self.request)


class DownloadData(GeoJSONMixin, DetailView):

    model = Map
    pk_url_kwarg = 'map_id'
    geojson_fields = ['name', 'datalayer_id', 'options', 'icon', 'description', 'color']

    def get_queryset(self):
        features = []
        for datalayer in self.object.datalayer_set.all():
            features += datalayer.features
        return features

    def get_object(self):
        return get_object_or_404(Map, pk=self.kwargs[self.pk_url_kwarg])

    def render_to_response(self, context):
        response = simple_json_response(**self.geojson(context))
        response['Content-Type'] = "application/json"
        response['Content-Disposition'] = 'attachment; filename="features.json"'
        return response


class EmbedMap(DetailView):
    model = Map
    template_name = "leaflet_storage/map_embed.html"
    pk_url_kwarg = 'map_id'

    def get_context_data(self, **kwargs):
        site_url = (settings.SHORT_SITE_URL if hasattr(settings, 'SHORT_SITE_URL')
                   else settings.SITE_URL if hasattr(settings, 'SITE_URL')
                   else 'http://%s' % self.request.META['HTTP_HOST'])
        iframe_url = map_url = '%s%s' % (site_url, self.object.get_absolute_url())
        qs_kwargs = {
            'allowEdit': 0,
            'embedControl': 0,
            'homeControl': 0,
            'locateControl': 0,
            'jumpToLocationControl': 0,
            'editInOSMControl': 0,
            'scaleControl': 0,
            'miniMap': 0,
            'tileLayersControl': 0,
            'scrollWheelZoom': 0,
        }
        query_string = "&".join("%s=%s" % (k, v) for k, v in qs_kwargs.iteritems())
        iframe_url = "%s?%s" % (iframe_url, query_string)
        short_url_name = getattr(settings, 'MAP_SHORT_URL_NAME', 'map_short_url')
        map_short_url = "%s%s" % (site_url, reverse_lazy(short_url_name, kwargs={'pk': self.object.pk}))
        kwargs.update({
            'map_url': map_url,
            'iframe_url': iframe_url,
            'map_short_url': map_short_url,
            'download_form': DownloadDataForm()
        })
        return super(EmbedMap, self).get_context_data(**kwargs)

    def render_to_response(self, context, **response_kwargs):
        return render_to_json(self.get_template_names(), response_kwargs, context, self.request)


class MapDelete(DeleteView):
    model = Map
    pk_url_kwarg = "map_id"

    def render_to_response(self, context, **response_kwargs):
        return render_to_json(self.get_template_names(), response_kwargs, context, self.request)

    def delete(self, *args, **kwargs):
        self.object = self.get_object()
        if not self.request.user == self.object.owner:
            return HttpResponseForbidden(_('Only its owner can delete the map.'))
        self.object.delete()
        return simple_json_response(redirect="/")

    def get_context_data(self, **kwargs):
        kwargs.update({
            'action_url': reverse_lazy('map_delete', kwargs={'map_id': self.kwargs['map_id']})
        })
        return super(MapDelete, self).get_context_data(**kwargs)


class MapClone(View):

    def get(self, *args, **kwargs):
        if not getattr(settings, "LEAFLET_STORAGE_ALLOW_ANONYMOUS", False) \
           and not self.request.user.is_authenticated():
            return HttpResponseForbidden('Forbidden')
        owner = self.request.user if self.request.user.is_authenticated() else None
        self.object = kwargs['map_inst'].clone(owner=owner)
        response = simple_json_response(redirect=self.object.get_absolute_url())
        if not self.request.user.is_authenticated():
            key, value = self.object.signed_cookie_elements
            response.set_signed_cookie(key, value)
            anonymous_url = "%s%s" % (
                settings.SITE_URL,
                self.object.get_anonymous_edit_url()
            )
            msg = _(
                "Your map has been cloned! If you want to edit this map from "
                "another computer, please use this link: %(anonymous_url)s"
                % {"anonymous_url": anonymous_url}
            )
        else:
            msg = _("Congratulations, your map has been cloned!")
        messages.info(self.request, msg)
        return response


class MapShortUrl(RedirectView):
    query_string = True

    def get_redirect_url(self, **kwargs):
        map_inst = get_object_or_404(Map, pk=kwargs['pk'])
        url = map_inst.get_absolute_url()
        if self.query_string:
            args = self.request.META.get('QUERY_STRING', '')
            if args:
                url = "%s?%s" % (url, args)
        return url


class MapOldUrl(RedirectView):
    """
    Handle map URLs from before anonymous allowing.
    """
    query_string = True

    def get_redirect_url(self, **kwargs):
        owner = get_object_or_404(User, username=self.kwargs['username'])
        map_inst = get_object_or_404(Map, slug=self.kwargs['slug'], owner=owner)
        url = map_inst.get_absolute_url()
        if self.query_string:
            args = self.request.META.get('QUERY_STRING', '')
            if args:
                url = "%s?%s" % (url, args)
        return url


class MapAnonymousEditUrl(RedirectView):

    def get(self, request, *args, **kwargs):
        signer = Signer()
        try:
            pk = signer.unsign(self.kwargs['signature'])
        except BadSignature:
            return HttpResponseForbidden('Bad Signature')
        else:
            map_inst = get_object_or_404(Map, pk=pk)
            url = map_inst.get_absolute_url()
            response = HttpResponseRedirect(url)
            if not map_inst.owner:
                key, value = map_inst.signed_cookie_elements
                response.set_signed_cookie(key, value)
            return response


# ############## #
#    Features    #
# ############## #


class FeatureGeoJSONListView(BaseListView, GeoJSONMixin):

    def get_queryset(self):
        datalayer = get_object_or_404(DataLayer, pk=self.kwargs['datalayer_id'])
        return datalayer.features

    def render_to_response(self, context, **response_kwargs):
        geoj = self.geojson(context)
        return HttpResponse(simplejson.dumps(geoj))


class FeatureView(DetailView):
    context_object_name = "feature"

    def render_to_response(self, context, **response_kwargs):
        return render_to_json(self.get_template_names(), response_kwargs, context, self.request)

    def get_template_names(self):
        """
        Add a fallback, but keep the default templates to make it easily
        extendable.
        """
        templates = super(FeatureView, self).get_template_names()
        templates.append("leaflet_storage/feature_detail.html")
        return templates


class FeatureAdd(CreateView):

    def get_success_url(self):
        return reverse_lazy(self.geojson_url, kwargs={"pk": self.object.pk})

    def render_to_response(self, context, **response_kwargs):
        return render_to_json(self.get_template_names(), response_kwargs, context, self.request)

    def get_form(self, form_class):
        form = super(FeatureAdd, self).get_form(form_class)
        map_inst = self.kwargs['map_inst']
        layers = DataLayer.objects.filter(map=map_inst)
        form.fields['datalayer'].queryset = layers
        if layers:
            form.fields['datalayer'].initial = layers[0]
        return form

    def get_template_names(self):
        """
        Add a fallback, but keep the default templates to make it easily
        extendable.
        """
        templates = super(FeatureAdd, self).get_template_names()
        templates.append("leaflet_storage/feature_form.html")
        return templates


class FeatureUpdate(UpdateView):

    def get_success_url(self):
        return reverse_lazy(self.geojson_url, kwargs={"pk": self.object.pk})

    def render_to_response(self, context, **response_kwargs):
        return render_to_json(self.get_template_names(), response_kwargs, context, self.request)

    def get_context_data(self, **kwargs):
        kwargs.update({
            'delete_url': reverse_lazy(self.delete_url, kwargs={'map_id': self.kwargs['map_id'], 'pk': self.object.pk})
        })
        return super(FeatureUpdate, self).get_context_data(**kwargs)

    # TODO: factorize with FeatureAdd!
    def get_form(self, form_class):
        form = super(FeatureUpdate, self).get_form(form_class)
        map_inst = self.kwargs['map_inst']
        form.fields['datalayer'].queryset = DataLayer.objects.filter(map=map_inst)
        return form

    def get_template_names(self):
        """
        Add a fallback, but keep the default templates to make it easily
        extendable.
        """
        templates = super(FeatureUpdate, self).get_template_names()
        templates.append("leaflet_storage/feature_form.html")
        return templates


class FeatureDelete(DeleteView):
    context_object_name = "feature"
    template_name = "leaflet_storage/feature_confirm_delete.html"

    def render_to_response(self, context, **response_kwargs):
        return render_to_json(self.get_template_names(), response_kwargs, context, self.request)

    def delete(self, *args, **kwargs):
        self.object = self.get_object()
        self.object.delete()
        return simple_json_response(
            #Translators: "feature" is a feature type: Marker, Polyline, Polygon
            info=_("%(feature)s successfully deleted." % {"feature": self.model._meta.verbose_name})
        )


class FeatureGeoJSON(BaseDetailView, GeoJSONMixin):

    def get_queryset(self):
        return self.model.objects.filter(pk=self.kwargs['pk'])

    def render_to_response(self, context):
        collection = self.geojson(context)
        try:
            geoj = collection['features'][0]
        except KeyError:
            return Http404()
        return HttpResponse(simplejson.dumps(geoj))


class MarkerGeoJSON(FeatureGeoJSON):
    model = Marker


class MarkerDelete(FeatureDelete):
    model = Marker


class MarkerView(FeatureView):
    model = Marker


class MarkerUpdate(FeatureUpdate):
    model = Marker
    geojson_url = 'marker_geojson'
    delete_url = "marker_delete"
    form_class = MarkerForm


class MarkerAdd(FeatureAdd):
    model = Marker
    geojson_url = 'marker_geojson'
    form_class = MarkerForm


class PolylineView(FeatureView):
    model = Polyline


class PolylineAdd(FeatureAdd):
    model = Polyline
    geojson_url = 'polyline_geojson'
    form_class = PolylineForm


class PolylineUpdate(FeatureUpdate):
    model = Polyline
    geojson_url = 'polyline_geojson'
    delete_url = "polyline_delete"
    form_class = PolylineForm


class PolylineDelete(FeatureDelete):
    model = Polyline


class PolylineGeoJSON(FeatureGeoJSON):
    model = Polyline


class PolygonView(FeatureView):
    model = Polygon


class PolygonAdd(FeatureAdd):
    model = Polygon
    geojson_url = 'polygon_geojson'
    form_class = PolygonForm


class PolygonUpdate(FeatureUpdate):
    model = Polygon
    geojson_url = 'polygon_geojson'
    delete_url = "polygon_delete"
    form_class = PolygonForm


class PolygonDelete(FeatureDelete):
    model = Polygon


class PolygonGeoJSON(FeatureGeoJSON):
    model = Polygon


# ############## #
#    DataLayer   #
# ############## #

class DataLayerView(BaseDetailView, GeoJSONMixin):
    model = DataLayer

    def render_to_response(self, context, **response_kwargs):
        if self.object.geojson:
            path = self.object.geojson.path
            statobj = os.stat(path)
            # TODO IMS
            response = CompatibleStreamingHttpResponse(
                open(path, 'rb'),
                content_type='application/json'
            )
            response["Last-Modified"] = http_date(statobj.st_mtime)
            return response
        else:
            # transitional
            return HttpResponse(simplejson.dumps(self.object.to_geojson()))


class DataLayerCreate(CreateView):
    model = DataLayer
    form_class = DataLayerForm

    def form_valid(self, form):
        form.instance.map = self.kwargs['map_inst']
        self.object = form.save()
        return simple_json_response(**self.object.metadata)


class DataLayerUpdate(UpdateView):
    model = DataLayer
    form_class = DataLayerForm

    def form_valid(self, form):
        self.object = form.save()
        return simple_json_response(**self.object.metadata)


class DataLayerDelete(DeleteView):
    model = DataLayer

    def delete(self, *args, **kwargs):
        self.object = self.get_object()
        self.object.delete()
        return simple_json_response(info=_("Layer successfully deleted."))


# ############## #
#     Picto      #
# ############## #

class PictogramJSONList(ListView):
    model = Pictogram

    def render_to_response(self, context, **response_kwargs):
        content = [p.json for p in Pictogram.objects.all()]
        return simple_json_response(pictogram_list=content)


# ############## #
#     Generic    #
# ############## #

def logout(request):
    do_logout(request)
    return simple_json_response(redirect="/")


class LoginPopupEnd(TemplateView):
    """
    End of a loggin process in popup.
    Basically close the popup.
    """
    template_name = "leaflet_storage/login_popup_end.html"
