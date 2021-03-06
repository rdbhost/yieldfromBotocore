# Copyright 2012-2014 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You
# may not use this file except in compliance with the License. A copy of
# the License is located at
#
# http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is
# distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

"""Builtin event handlers.

This module contains builtin handlers for events emitted by botocore.
"""

import base64
import hashlib
import logging
import xml.etree.cElementTree
import asyncio
import io
import copy

from .compat import urlsplit, urlunsplit, unquote, json, quote
from . import retryhandler
from . import utils
from . import translate
from . import UNSIGNED
#from . import auth as botoauth
from .signers import add_generate_presigned_url
from .signers import add_generate_presigned_post
from .docs.utils import AutoPopulatedParam
from .docs.utils import HideParamFromOperations
from .docs.utils import AppendParamDocumentation


logger = logging.getLogger(__name__)

REGISTER_FIRST = object()
REGISTER_LAST = object()



@asyncio.coroutine
def check_for_200_error(response, **kwargs):
    # From: http://docs.aws.amazon.com/AmazonS3/latest/API/RESTObjectCOPY.html
    # There are two opportunities for a copy request to return an error. One
    # can occur when Amazon S3 receives the copy request and the other can
    # occur while Amazon S3 is copying the files. If the error occurs before
    # the copy operation starts, you receive a standard Amazon S3 error. If the
    # error occurs during the copy operation, the error response is embedded in
    # the 200 OK response. This means that a 200 OK response can contain either
    # a success or an error. Make sure to design your application to parse the
    # contents of the response and handle it appropriately.
    #
    # So this handler checks for this case.  Even though the server sends a
    # 200 response, conceptually this should be handled exactly like a
    # 500 response (with respect to raising exceptions, retries, etc.)
    # We're connected *before* all the other retry logic handlers, so as long
    # as we switch the error code to 500, we'll retry the error as expected.
    if response is None:
        # A None response can happen if an exception is raised while
        # trying to retrieve the response.  See Endpoint._get_response().
        return
    http_response, parsed = response
    if (yield from _looks_like_special_case_error(http_response)):
        logger.debug("Error found for response with 200 status code, "
                     "errors: %s, changing status code to "
                     "500.", parsed)
        http_response.status_code = 500


@asyncio.coroutine
def _looks_like_special_case_error(http_response):
    if http_response.status_code == 200:
        parser = xml.etree.cElementTree.XMLParser(
            target=xml.etree.cElementTree.TreeBuilder(),
            encoding='utf-8')
        parser.feed((yield from http_response.content))
        root = parser.close()
        if root.tag == 'Error':
            return True
    return False


def decode_console_output(parsed, **kwargs):
    try:
        value = base64.b64decode(parsed['Output'].encode('latin1')).decode('utf-8')
        parsed['Output'] = value
    except (ValueError, TypeError, AttributeError):
        logger.debug('Error decoding base64', exc_info=True)


def decode_quoted_jsondoc(value):
    try:
        value = json.loads(unquote(value))
    except (ValueError, TypeError):
        logger.debug('Error loading quoted JSON', exc_info=True)
    return value


def json_decode_template_body(parsed, **kwargs):
    if 'TemplateBody' in parsed:
        try:
            value = json.loads(parsed['TemplateBody'])
            parsed['TemplateBody'] = value
        except (ValueError, TypeError):
            logger.debug('error loading JSON', exc_info=True)


def calculate_md5(params, **kwargs):
    request_dict = params
    if request_dict['body'] and 'Content-MD5' not in params['headers']:
        md5 = hashlib.md5()
        md5.update(params['body'].encode('latin-1'))
        value = base64.b64encode(md5.digest()).decode('utf-8')
        params['headers']['Content-MD5'] = value


def sse_md5(params, **kwargs):
    """
    S3 server-side encryption requires the encryption key to be sent to the
    server base64 encoded, as well as a base64-encoded MD5 hash of the
    encryption key. This handler does both if the MD5 has not been set by
    the caller.
    """
    if not _needs_s3_sse_customization(params):
        return
    key_as_bytes = params['SSECustomerKey']
    if isinstance(key_as_bytes, str):
        key_as_bytes = key_as_bytes.encode('utf-8')
    key_md5_str = base64.b64encode(
        hashlib.md5(key_as_bytes).digest()).decode('utf-8')
    key_b64_encoded = base64.b64encode(key_as_bytes).decode('utf-8')
    params['SSECustomerKey'] = key_b64_encoded
    params['SSECustomerKeyMD5'] = key_md5_str


def _needs_s3_sse_customization(params):
    return (params.get('SSECustomerKey') is not None and
            'SSECustomerKeyMD5' not in params)


def register_retries_for_service(service_data, session,
                                 service_name, **kwargs):
    loader = session.get_component('data_loader')
    endpoint_prefix = service_data.get('metadata', {}).get('endpointPrefix')
    if endpoint_prefix is None:
        logger.debug("Not registering retry handlers, could not endpoint "
                     "prefix from model for service %s", service_name)
        return
    config = _load_retry_config(loader, endpoint_prefix)
    if not config:
        return
    logger.debug("Registering retry handlers for service: %s", service_name)
    handler = retryhandler.create_retry_handler(
        config, endpoint_prefix)
    unique_id = 'retry-config-%s' % endpoint_prefix
    session.register('needs-retry.%s' % endpoint_prefix,
                     handler, unique_id=unique_id)
    _register_for_operations(config, session,
                             service_name=endpoint_prefix)


def _load_retry_config(loader, endpoint_prefix):
    original_config = loader.load_data('_retry')
    retry_config = translate.build_retry_config(
        endpoint_prefix, original_config['retry'],
        original_config.get('definitions', {}))
    return retry_config


def _register_for_operations(config, session, service_name):
    # There's certainly a tradeoff for registering the retry config
    # for the operations when the service is created.  In practice,
    # there aren't a whole lot of per operation retry configs so
    # this is ok for now.
    for key in config:
        if key == '__default__':
            continue
        handler = retryhandler.create_retry_handler(config, key)
        unique_id = 'retry-config-%s-%s' % (service_name, key)
        session.register('needs-retry.%s.%s' % (service_name, key),
                         handler, unique_id=unique_id)


def disable_signing(**kwargs):
    """
    This handler disables request signing by setting the signer
    name to a special sentinel value.
    """
    return UNSIGNED


def add_expect_header(model, params, **kwargs):
    if model.http.get('method', '') not in ['PUT', 'POST']:
        return
    if 'body' in params:
        body = params['body']
        if hasattr(body, 'read'):
            # Any file like object will use an expect 100-continue
            # header regardless of size.
            logger.debug("Adding expect 100 continue header to request.")
            params['headers']['Expect'] = '100-continue'


def quote_source_header(params, **kwargs):
    if params['headers'] and 'x-amz-copy-source' in params['headers']:
        value = params['headers']['x-amz-copy-source']
        p = urlsplit(value)
        # We only want to quote the path.  If the user specified
        # extra parts, say '?versionId=myversionid' then that part
        # should not be quoted.
        quoted = quote(p[2].encode('utf-8'), '/~')
        final_source = urlunsplit((p[0], p[1], quoted, p[3], p[4]))
        params['headers']['x-amz-copy-source'] = final_source

@asyncio.coroutine
def copy_snapshot_encrypted(params, request_signer, **kwargs):
    # The presigned URL that facilities copying an encrypted snapshot.
    # If the user does not provide this value, we will automatically
    # calculate on behalf of the user and inject the PresignedUrl
    # into the requests.
    # The params sent in the event don't quite sync up 100% so we're
    # renaming them here until they can be updated in the event.
    request_dict = params
    params = request_dict['body']
    if 'PresignedUrl' in params:
        # If the customer provided this value, then there's nothing for
        # us to do.
        return
    destination_region = request_signer._region_name
    params['DestinationRegion'] = destination_region
    # The request will be sent to the destination region, so we need
    # to create an endpoint to the source region and create a presigned
    # url based on the source endpoint.
    source_region = params['SourceRegion']

    # The better way to do this is to actually get the
    # endpoint_resolver and get the endpoint_url given the
    # source region.  In this specific case, we know that
    # we can safely replace the dest region with the source
    # region because of the supported EC2 regions, but in
    # general this is not a safe assumption to make.
    # I think eventually we should try to plumb through something
    # that allows us to resolve endpoints from regions.
    request_dict_copy = copy.deepcopy(request_dict)
    request_dict_copy['url'] = request_dict['url'].replace(
        destination_region, source_region)
    request_dict_copy['method'] = 'GET'
    request_dict_copy['headers'] = {}
    presigned_url = request_signer.generate_presigned_url(
        request_dict_copy, region_name=source_region)
    params['PresignedUrl'] = presigned_url


def json_decode_policies(parsed, model, **kwargs):
    # Any time an IAM operation returns a policy document
    # it is a string that is json that has been urlencoded,
    # i.e urlencode(json.dumps(policy_document)).
    # To give users something more useful, we will urldecode
    # this value and json.loads() the result so that they have
    # the policy document as a dictionary.
    output_shape = model.output_shape
    if output_shape is not None:
        _decode_policy_types(parsed, model.output_shape)


def _decode_policy_types(parsed, shape):
    # IAM consistently uses the policyDocumentType shape to indicate
    # strings that have policy documents.
    shape_name = 'policyDocumentType'
    if shape.type_name == 'structure':
        for member_name, member_shape in shape.members.items():
            if member_shape.type_name == 'string' and \
                    member_shape.name == shape_name and \
                    member_name in parsed:
                parsed[member_name] = decode_quoted_jsondoc(
                    parsed[member_name])
            elif member_name in parsed:
                _decode_policy_types(parsed[member_name], member_shape)
    if shape.type_name == 'list':
        shape_member = shape.member
        for item in parsed:
            _decode_policy_types(item, shape_member)


@asyncio.coroutine
def parse_get_bucket_location(parsed, http_response, **kwargs):
    # s3.GetBucketLocation cannot be modeled properly.  To
    # account for this we just manually parse the XML document.
    # The "parsed" passed in only has the ResponseMetadata
    # filled out.  This handler will fill in the LocationConstraint
    # value.
    response_body = yield from http_response.content
    parser = xml.etree.cElementTree.XMLParser(
        target=xml.etree.cElementTree.TreeBuilder(),
        encoding='utf-8')
    parser.feed(response_body)
    root = parser.close()
    region = root.text
    parsed['LocationConstraint'] = region


def base64_encode_user_data(params, **kwargs):
    if 'UserData' in params:
        if isinstance(params['UserData'], str):
            # Encode it to bytes if it is text.
            params['UserData'] = params['UserData'].encode('utf-8')
        params['UserData'] = base64.b64encode(
            params['UserData']).decode('utf-8')


def document_base64_encoding():
    description = 'UserData will be automatically base64 encoded if necessary.'
    append = AppendParamDocumentation('UserData', description)
    return append.append_documentation


def fix_route53_ids(params, model, **kwargs):
    """
    Check for and split apart Route53 resource IDs, setting
    only the last piece. This allows the output of one operation
    (e.g. ``'foo/1234'``) to be used as input in another
    operation (e.g. it expects just ``'1234'``).
    """
    input_shape = model.input_shape
    if not input_shape or not hasattr(input_shape, 'members'):
        return

    members = [name for (name, shape) in input_shape.members.items()
               if shape.name in ['ResourceId', 'DelegationSetId']]

    for name in members:
        if name in params:
            orig_value = params[name]
            params[name] = orig_value.split('/')[-1]
            logger.debug('%s %s -> %s', name, orig_value, params[name])


def inject_account_id(params, **kwargs):
    if params.get('accountId') is None:
        # Glacier requires accountId, but allows you
        # to specify '-' for the current owners account.
        # We add this default value if the user does not
        # provide the accountId as a convenience.
        params['accountId'] = '-'


def add_glacier_version(model, params, **kwargs):
    request_dict = params
    request_dict['headers']['x-amz-glacier-version'] = model.metadata[
        'apiVersion']


def add_glacier_checksums(params, **kwargs):
    """Add glacier checksums to the http request.

    This will add two headers to the http request:

        * x-amz-content-sha256
        * x-amz-sha256-tree-hash

    These values will only be added if they are not present
    in the HTTP request.

    """
    request_dict = params
    headers = request_dict['headers']
    body = request_dict['body']
    if isinstance(body, bytes):
        # If the user provided a bytes type instead of a file
        # like object, we're temporarily create a BytesIO object
        # so we can use the util functions to calculate the
        # checksums which assume file like objects.  Note that
        # we're not actually changing the body in the request_dict.
        body = io.BytesIO(body)
    starting_position = body.tell()
    if 'x-amz-content-sha256' not in headers:
        headers['x-amz-content-sha256'] = utils.calculate_sha256(
            body, as_hex=True)
    body.seek(starting_position)
    if 'x-amz-sha256-tree-hash' not in headers:
        headers['x-amz-sha256-tree-hash'] = utils.calculate_tree_hash(body)
    body.seek(starting_position)


def switch_host_machinelearning(request, **kwargs):
    switch_host_with_param(request, 'PredictEndpoint')


def switch_host_with_param(request, param_name):
    request_json = json.loads(request.data.decode('utf-8'))
    if request_json.get(param_name):
        new_endpoint = request_json[param_name]
        new_endpoint_components = urlsplit(new_endpoint)
        original_endpoint = request.url
        original_endpoint_components = urlsplit(original_endpoint)
        final_endpoint_components = (
            new_endpoint_components.scheme,
            new_endpoint_components.netloc,
            original_endpoint_components.path,
            original_endpoint_components.query,
            ''
        )
        final_endpoint = urlunsplit(final_endpoint_components)
        request.url = final_endpoint


# This is a list of (event_name, handler).
# When a Session is created, everything in this list will be
# automatically registered with that Session.

BUILTIN_HANDLERS = [
    ('creating-client-class', add_generate_presigned_url),
    ('creating-client-class.s3', add_generate_presigned_post),
    ('after-call.iam', json_decode_policies),

    ('after-call.ec2.GetConsoleOutput', decode_console_output),
    ('after-call.cloudformation.GetTemplate', json_decode_template_body),
    ('after-call.s3.GetBucketLocation', parse_get_bucket_location),

    ('before-call.s3.PutBucketTagging', calculate_md5),
    ('before-call.s3.PutBucketLifecycle', calculate_md5),
    ('before-call.s3.PutBucketCors', calculate_md5),
    ('before-call.s3.DeleteObjects', calculate_md5),
    ('before-call.s3.PutBucketReplication', calculate_md5),
    ('before-call.s3.UploadPartCopy', quote_source_header),
    ('before-call.s3.CopyObject', quote_source_header),
    ('before-call.s3', add_expect_header),
    ('before-call.glacier', add_glacier_version),
    ('before-call.glacier.UploadArchive', add_glacier_checksums),
    ('before-call.glacier.UploadMultipartPart', add_glacier_checksums),
    ('before-call.ec2.CopySnapshot', copy_snapshot_encrypted),
    ('request-created.machinelearning.Predict', switch_host_machinelearning),
    ('needs-retry.s3.UploadPartCopy', check_for_200_error, REGISTER_FIRST),
    ('needs-retry.s3.CopyObject', check_for_200_error, REGISTER_FIRST),
    ('needs-retry.s3.CompleteMultipartUpload', check_for_200_error,
     REGISTER_FIRST),
    ('service-data-loaded', register_retries_for_service),
    ('choose-signer.cognito-identity.GetId', disable_signing),
    ('choose-signer.cognito-identity.GetOpenIdToken', disable_signing),
    ('choose-signer.sts.AssumeRoleWithSAML', disable_signing),
    ('before-sign.s3', utils.fix_s3_host),
    ('before-parameter-build.s3.HeadObject', sse_md5),
    ('before-parameter-build.s3.GetObject', sse_md5),
    ('before-parameter-build.s3.PutObject', sse_md5),
    ('before-parameter-build.s3.CopyObject', sse_md5),
    ('before-parameter-build.s3.CreateMultipartUpload', sse_md5),
    ('before-parameter-build.s3.UploadPart', sse_md5),
    ('before-parameter-build.s3.UploadPartCopy', sse_md5),
    ('before-parameter-build.ec2.RunInstances', base64_encode_user_data),
    ('before-parameter-build.autoscaling.CreateLaunchConfiguration',
     base64_encode_user_data),
    ('before-parameter-build.route53', fix_route53_ids),
    ('before-parameter-build.glacier', inject_account_id),

    # Glacier documentation customizations
    ('docs.*.glacier.*.complete-section',
     AutoPopulatedParam('accountId', 'Note: this parameter is set to "-" by \
                         default if no value is not specified.')
     .document_auto_populated_param),
    ('docs.*.glacier.*.complete-section',
     AutoPopulatedParam('checksum').document_auto_populated_param),
    # UserData base64 encoding documentation customizations
    ('docs.*.ec2.RunInstances.complete-section', document_base64_encoding()),
    ('docs.*.autoscaling.CreateLaunchConfiguration.complete-section',
     document_base64_encoding()),
    # EC2 CopySnapshot documentation customizations
    ('docs.*.ec2.CopySnapshot.complete-section',
     AutoPopulatedParam('PresignedUrl').document_auto_populated_param),
    ('docs.*.ec2.CopySnapshot.complete-section',
     AutoPopulatedParam('DestinationRegion').document_auto_populated_param),
    # S3 SSE documentation modifications
    ('docs.*.s3.*.complete-section',
     AutoPopulatedParam('SSECustomerKeyMD5').document_auto_populated_param),
    # The following S3 operations cannot actually accept a ContentMD5
    ('docs.*.s3.*.complete-section',
     HideParamFromOperations(
         's3', 'ContentMD5',
         ['DeleteObjects', 'PutBucketAcl', 'PutBucketCors',
          'PutBucketLifecycle', 'PutBucketLogging', 'PutBucketNotification',
          'PutBucketPolicy', 'PutBucketReplication', 'PutBucketRequestPayment',
          'PutBucketTagging', 'PutBucketVersioning', 'PutBucketWebsite',
          'PutObjectAcl']).hide_param)
]
