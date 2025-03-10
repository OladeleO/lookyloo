#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import json
from typing import Any, Dict

import flask_login  # type: ignore
from flask import request, send_file
from flask_restx import Namespace, Resource, abort, fields  # type: ignore
from werkzeug.security import check_password_hash

from lookyloo.helpers import splash_status
from lookyloo.lookyloo import Lookyloo

from .helpers import build_users_table, load_user_from_request, src_request_ip

api = Namespace('GenericAPI', description='Generic Lookyloo API', path='/')


lookyloo: Lookyloo = Lookyloo()


def api_auth_check(method):
    if flask_login.current_user.is_authenticated or load_user_from_request(request):
        return method
    abort(403, 'Authentication required.')


token_request_fields = api.model('AuthTokenFields', {
    'username': fields.String(description="Your username", required=True),
    'password': fields.String(description="Your password", required=True),
})


@api.route('/json/get_token')
@api.doc(description='Get the API token required for authenticated calls')
class AuthToken(Resource):

    users_table = build_users_table()

    @api.param('username', 'Your username')
    @api.param('password', 'Your password')
    def get(self):
        username = request.args['username'] if request.args.get('username') else False
        password = request.args['password'] if request.args.get('password') else False
        if username in self.users_table and check_password_hash(self.users_table[username]['password'], password):
            return {'authkey': self.users_table[username]['authkey']}
        return {'error': 'User/Password invalid.'}, 401

    @api.doc(body=token_request_fields)
    def post(self):
        auth: Dict = request.get_json(force=True)
        if 'username' in auth and 'password' in auth:  # Expected keys in json
            if (auth['username'] in self.users_table
                    and check_password_hash(self.users_table[auth['username']]['password'], auth['password'])):
                return {'authkey': self.users_table[auth['username']]['authkey']}
        return {'error': 'User/Password invalid.'}, 401


@api.route('/json/splash_status')
@api.doc(description='Get status of splash.')
class SplashStatus(Resource):
    def get(self):
        status, info = splash_status()
        return {'is_up': status, 'info': info}


@api.route('/json/<string:capture_uuid>/status')
@api.doc(description='Get the status of a capture',
         params={'capture_uuid': 'The UUID of the capture'})
class CaptureStatusQuery(Resource):
    def get(self, capture_uuid: str):
        return {'status_code': lookyloo.get_capture_status(capture_uuid)}


@api.route('/json/<string:capture_uuid>/hostnames')
@api.doc(description='Get all the hostnames of all the resources of a capture',
         params={'capture_uuid': 'The UUID of the capture'})
class CaptureHostnames(Resource):
    def get(self, capture_uuid: str):
        cache = lookyloo.capture_cache(capture_uuid)
        if not cache:
            return {'error': 'UUID missing in cache, try again later.'}, 400
        to_return: Dict[str, Any] = {'response': {'hostnames': list(lookyloo.get_hostnames(capture_uuid))}}
        return to_return


@api.route('/json/<string:capture_uuid>/urls')
@api.doc(description='Get all the URLs of all the resources of a capture',
         params={'capture_uuid': 'The UUID of the capture'})
class CaptureURLs(Resource):
    def get(self, capture_uuid: str):
        cache = lookyloo.capture_cache(capture_uuid)
        if not cache:
            return {'error': 'UUID missing in cache, try again later.'}, 400
        to_return: Dict[str, Any] = {'response': {'urls': list(lookyloo.get_urls(capture_uuid))}}
        return to_return


@api.route('/json/<string:capture_uuid>/hashes')
@api.doc(description='Get all the hashes of all the resources of a capture',
         params={'capture_uuid': 'The UUID of the capture'})
class CaptureHashes(Resource):
    def get(self, capture_uuid: str):
        cache = lookyloo.capture_cache(capture_uuid)
        if not cache:
            return {'error': 'UUID missing in cache, try again later.'}, 400
        to_return: Dict[str, Any] = {'response': {'hashes': list(lookyloo.get_hashes(capture_uuid))}}
        return to_return


@api.route('/json/<string:capture_uuid>/redirects')
@api.doc(description='Get all the redirects of a capture',
         params={'capture_uuid': 'The UUID of the capture'})
class CaptureRedirects(Resource):
    def get(self, capture_uuid: str):
        cache = lookyloo.capture_cache(capture_uuid)
        if not cache:
            return {'error': 'UUID missing in cache, try again later.'}, 400

        to_return: Dict[str, Any] = {'response': {'url': cache.url, 'redirects': []}}
        if not cache.redirects:
            to_return['response']['info'] = 'No redirects'
            return to_return
        if cache.incomplete_redirects:
            # Trigger tree build, get all redirects
            lookyloo.get_crawled_tree(capture_uuid)
            cache = lookyloo.capture_cache(capture_uuid)
            if cache:
                to_return['response']['redirects'] = cache.redirects
        else:
            to_return['response']['redirects'] = cache.redirects

        return to_return


@api.route('/json/<string:capture_uuid>/misp_export')
@api.doc(description='Get an export of the capture in MISP format',
         params={'capture_uuid': 'The UUID of the capture'})
class MISPExport(Resource):
    def get(self, capture_uuid: str):
        with_parents = request.args.get('with_parents')
        event = lookyloo.misp_export(capture_uuid, True if with_parents else False)
        if isinstance(event, dict):
            return event

        to_return = []
        for e in event:
            to_return.append(e.to_json(indent=2))
        return to_return


misp_push_fields = api.model('MISPPushFields', {
    'allow_duplicates': fields.Integer(description="Push the event even if it is already present on the MISP instance",
                                       example=0, min=0, max=1),
    'with_parents': fields.Integer(description="Also push the parents of the capture (if any)",
                                   example=0, min=0, max=1),
})


@api.route('/json/<string:capture_uuid>/misp_push')
@api.doc(description='Push an event to a pre-configured MISP instance',
         params={'capture_uuid': 'The UUID of the capture'},
         security='apikey')
class MISPPush(Resource):
    method_decorators = [api_auth_check]

    @api.param('with_parents', 'Also push the parents of the capture (if any)')
    @api.param('allow_duplicates', 'Push the event even if it is already present on the MISP instance')
    def get(self, capture_uuid: str):
        with_parents = True if request.args.get('with_parents') else False
        allow_duplicates = True if request.args.get('allow_duplicates') else False
        to_return: Dict = {}
        if not lookyloo.misp.available:
            to_return['error'] = 'MISP module not available.'
        elif not lookyloo.misp.enable_push:
            to_return['error'] = 'Push not enabled in MISP module.'
        else:
            event = lookyloo.misp_export(capture_uuid, with_parents)
            if isinstance(event, dict):
                to_return['error'] = event
            else:
                new_events = lookyloo.misp.push(event, allow_duplicates)
                if isinstance(new_events, dict):
                    to_return['error'] = new_events
                else:
                    events_to_return = []
                    for e in new_events:
                        events_to_return.append(e.to_json(indent=2))
                    return events_to_return

        return to_return

    @api.doc(body=misp_push_fields)
    def post(self, capture_uuid: str):
        parameters: Dict = request.get_json(force=True)
        with_parents = True if parameters.get('with_parents') else False
        allow_duplicates = True if parameters.get('allow_duplicates') else False

        to_return: Dict = {}
        if not lookyloo.misp.available:
            to_return['error'] = 'MISP module not available.'
        elif not lookyloo.misp.enable_push:
            to_return['error'] = 'Push not enabled in MISP module.'
        else:
            event = lookyloo.misp_export(capture_uuid, with_parents)
            if isinstance(event, dict):
                to_return['error'] = event
            else:
                new_events = lookyloo.misp.push(event, allow_duplicates)
                if isinstance(new_events, dict):
                    to_return['error'] = new_events
                else:
                    events_to_return = []
                    for e in new_events:
                        events_to_return.append(e.to_json(indent=2))
                    return events_to_return

        return to_return


trigger_modules_fields = api.model('TriggerModulesFields', {
    'force': fields.Boolean(description="Force trigger the modules, even if the results are already cached.",
                            default=False, required=False),
})


@api.route('/json/<string:capture_uuid>/trigger_modules')
@api.doc(description='Trigger all the available 3rd party modules on the given capture',
         params={'capture_uuid': 'The UUID of the capture'})
class TriggerModules(Resource):
    @api.doc(body=trigger_modules_fields)
    def post(self, capture_uuid: str):
        parameters: Dict = request.get_json(force=True)
        force = True if parameters.get('force') else False
        return lookyloo.trigger_modules(capture_uuid, force=force)


@api.route('/json/hash_info/<h>')
@api.doc(description='Search for a ressource with a specific hash (sha512)',
         params={'h': 'The hash (sha512)'})
class HashInfo(Resource):
    def get(self, h: str):
        details, body = lookyloo.get_body_hash_full(h)
        if not details:
            return {'error': 'Unknown Hash.'}, 400
        to_return: Dict[str, Any] = {'response': {'hash': h, 'details': details,
                                                  'body': base64.b64encode(body.getvalue()).decode()}}
        return to_return


url_info_fields = api.model('URLInfoFields', {
    'url': fields.String(description="The URL to search", required=True),
    'limit': fields.Integer(description="The maximal amount of captures to return", example=20),
})


@api.route('/json/url_info')
@api.doc(description='Search for a URL')
class URLInfo(Resource):

    @api.doc(body=url_info_fields)
    def post(self):
        to_query: Dict = request.get_json(force=True)
        occurrences = lookyloo.get_url_occurrences(to_query.pop('url'), **to_query)
        return occurrences


hostname_info_fields = api.model('HostnameInfoFields', {
    'hostname': fields.String(description="The hostname to search", required=True),
    'limit': fields.Integer(description="The maximal amount of captures to return", example=20),
})


@api.route('/json/hostname_info')
@api.doc(description='Search for a hostname')
class HostnameInfo(Resource):

    @api.doc(body=hostname_info_fields)
    def post(self):
        to_query: Dict = request.get_json(force=True)
        occurrences = lookyloo.get_hostname_occurrences(to_query.pop('hostname'), **to_query)
        return occurrences


@api.route('/json/stats')
@api.doc(description='Get the statistics of the lookyloo instance.')
class InstanceStats(Resource):
    def get(self):
        return lookyloo.get_stats()


@api.route('/json/<string:capture_uuid>/stats')
@api.doc(description='Get the statistics of the capture.',
         params={'capture_uuid': 'The UUID of the capture'})
class CaptureStats(Resource):
    def get(self, capture_uuid: str):
        return lookyloo.get_statistics(capture_uuid)


@api.route('/json/<string:capture_uuid>/info')
@api.doc(description='Get basic information about the capture.',
         params={'capture_uuid': 'The UUID of the capture'})
class CaptureInfo(Resource):
    def get(self, capture_uuid: str):
        return lookyloo.get_info(capture_uuid)


@api.route('/json/<string:capture_uuid>/cookies')
@api.doc(description='Get the complete cookie jar created during the capture.',
         params={'capture_uuid': 'The UUID of the capture'})
class CaptureCookies(Resource):
    def get(self, capture_uuid: str):
        return json.loads(lookyloo.get_cookies(capture_uuid).read())


# Just text

submit_fields_post = api.model('SubmitFieldsPost', {
    'url': fields.Url(description="The URL to capture", required=True),
    'listing': fields.Integer(description="Display the capture on the index", min=0, max=1, example=1),
    'user_agent': fields.String(description="User agent to use for the capture", example=''),
    'referer': fields.String(description="Referer to pass to the capture", example=''),
    'proxy': fields.Url(description="Proxy to use for the capture. Format: [scheme]://[username]:[password]@[hostname]:[port]", example=''),
    'cookies': fields.String(description="JSON export of a list of cookies as exported from an other capture", example='')
})


@api.route('/submit')
class SubmitCapture(Resource):

    @api.param('url', 'The URL to capture', required=True)
    @api.param('listing', 'Display the capture on the index', default=1)
    @api.param('user_agent', 'User agent to use for the capture')
    @api.param('referer', 'Referer to pass to the capture')
    @api.param('proxy', 'Proxy to use for the the capture')
    @api.produces(['text/text'])
    def get(self):
        if flask_login.current_user.is_authenticated:
            user = flask_login.current_user.get_id()
        else:
            user = src_request_ip(request)

        if 'url' not in request.args or not request.args.get('url'):
            return 'No "url" in the URL params, nothting to capture.', 400

        to_query = {'url': request.args['url'],
                    'listing': False if 'listing' in request.args and request.args['listing'] in [0, '0'] else True}
        if request.args.get('user_agent'):
            to_query['user_agent'] = request.args['user_agent']
        if request.args.get('referer'):
            to_query['referer'] = request.args['referer']
        if request.args.get('proxy'):
            to_query['proxy'] = request.args['proxy']

        perma_uuid = lookyloo.enqueue_capture(to_query, source='api', user=user, authenticated=flask_login.current_user.is_authenticated)
        return perma_uuid

    @api.doc(body=submit_fields_post)
    @api.produces(['text/text'])
    def post(self):
        if flask_login.current_user.is_authenticated:
            user = flask_login.current_user.get_id()
        else:
            user = src_request_ip(request)
        to_query: Dict = request.get_json(force=True)
        perma_uuid = lookyloo.enqueue_capture(to_query, source='api', user=user, authenticated=flask_login.current_user.is_authenticated)
        return perma_uuid


# Binary stuff

@api.route('/bin/<string:capture_uuid>/screenshot')
@api.doc(description='Get the screenshot associated to the capture.',
         params={'capture_uuid': 'The UUID of the capture'})
class CaptureScreenshot(Resource):

    @api.produces(['image/png'])
    def get(self, capture_uuid: str):
        return send_file(lookyloo.get_screenshot(capture_uuid), mimetype='image/png')


@api.route('/bin/<string:capture_uuid>/export')
@api.doc(description='Get all the files generated by the capture, except the pickle.',
         params={'capture_uuid': 'The UUID of the capture'})
class CaptureExport(Resource):

    @api.produces(['application/zip'])
    def get(self, capture_uuid: str):
        return send_file(lookyloo.get_capture(capture_uuid), mimetype='application/zip')


# Admin stuff

@api.route('/admin/rebuild_all')
@api.doc(description='Rebuild all the trees. WARNING: IT IS GOING TO TAKE A VERY LONG TIME.',
         security='apikey')
class RebuildAll(Resource):
    method_decorators = [api_auth_check]

    def post(self):
        try:
            lookyloo.rebuild_all()
        except Exception as e:
            return {'error': f'Unable to rebuild all captures: {e}.'}, 400
        else:
            return {'info': 'Captures successfully rebuilt.'}


@api.route('/admin/rebuild_all_cache')
@api.doc(description='Rebuild all the caches. It will take a while, but less that rebuild all.',
         security='apikey')
class RebuildAllCache(Resource):
    method_decorators = [api_auth_check]

    def post(self):
        try:
            lookyloo.rebuild_cache()
        except Exception as e:
            return {'error': f'Unable to rebuild all the caches: {e}.'}, 400
        else:
            return {'info': 'All caches successfully rebuilt.'}


@api.route('/admin/<string:capture_uuid>/rebuild')
@api.doc(description='Rebuild the tree.',
         params={'capture_uuid': 'The UUID of the capture'},
         security='apikey')
class CaptureRebuildTree(Resource):
    method_decorators = [api_auth_check]

    def post(self, capture_uuid):
        try:
            lookyloo.remove_pickle(capture_uuid)
            lookyloo.get_crawled_tree(capture_uuid)
        except Exception as e:
            return {'error': f'Unable to rebuild tree: {e}.'}, 400
        else:
            return {'info': f'Tree {capture_uuid} successfully rebuilt.'}


@api.route('/admin/<string:capture_uuid>/hide')
@api.doc(description='Hide the capture from the index.',
         params={'capture_uuid': 'The UUID of the capture'},
         security='apikey')
class CaptureHide(Resource):
    method_decorators = [api_auth_check]

    def post(self, capture_uuid):
        try:
            lookyloo.hide_capture(capture_uuid)
        except Exception as e:
            return {'error': f'Unable to hide the tree: {e}.'}, 400
        else:
            return {'info': f'Capture {capture_uuid} successfully hidden.'}
