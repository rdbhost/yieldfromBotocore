# Copyright 2014 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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

# This file altered by David Keeney 2015, as part of conversion to
# asyncio.
#
import asyncio
import sys, os
import time
import logging
import datetime
from tests import unittest, random_chars
import unittest
from io import StringIO

import yieldfrom.botocore.session
from yieldfrom.botocore.client import ClientError
from yieldfrom.botocore.exceptions import EndpointConnectionError

sys.path.append('..')
from asyncio_test_utils import async_test

os.environ['PYTHONASYNCIODEBUG'] = '1'
logging.basicConfig(level=logging.DEBUG)


class TestBucketWithVersions(unittest.TestCase):
    @asyncio.coroutine
    def set_up(self):
        self.session = yieldfrom.botocore.session.get_session()
        self.client = yield from self.session.create_client('s3', region_name='us-west-2')
        self.bucket_name = 'botocoretest%s' % random_chars(50)

    def extract_version_ids(self, versions):
        version_ids = []
        for marker in versions['DeleteMarkers']:
            version_ids.append(marker['VersionId'])
        for version in versions['Versions']:
            version_ids.append(version['VersionId'])
        return version_ids

    @async_test
    def test_create_versioned_bucket(self):
        # Verifies we can:
        # 1. Create a bucket
        # 2. Enable versioning
        # 3. Put an Object
        yield from self.client.create_bucket(Bucket=self.bucket_name)
        self.addCleanup(self.client.delete_bucket, Bucket=self.bucket_name)

        yield from self.client.put_bucket_versioning(
            Bucket=self.bucket_name,
            VersioningConfiguration={"Status": "Enabled"})
        response = yield from self.client.put_object(
            Bucket=self.bucket_name, Key='testkey', Body='bytes body')
        self.addCleanup(self.client.delete_object,
                        Bucket=self.bucket_name,
                        Key='testkey',
                        VersionId=response['VersionId'])

        response = yield from self.client.get_object(
            Bucket=self.bucket_name, Key='testkey')
        self.assertEqual((yield from response['Body'].read()), b'bytes body')

        response = yield from self.client.delete_object(Bucket=self.bucket_name,
                                             Key='testkey')
        # This cleanup step removes the DeleteMarker that's created
        # from the delete_object call above.
        self.addCleanup(self.client.delete_object,
                        Bucket=self.bucket_name,
                        Key='testkey',
                        VersionId=response['VersionId'])
        # Object does not exist anymore.
        with self.assertRaises(ClientError):
            yield from self.client.get_object(Bucket=self.bucket_name, Key='testkey')
        versions = yield from self.client.list_object_versions(Bucket=self.bucket_name)
        version_ids = self.extract_version_ids(versions)
        self.assertEqual(len(version_ids), 2)


# This is really a combination of testing the debug logging mechanism
# as well as the response wire log, which theoretically could be
# implemented in any number of modules, which makes it hard to pick
# which integration test module this code should live in, so I picked
# the client module.
class TestResponseLog(unittest.TestCase):

    @async_test
    def test_debug_log_contains_headers_and_body(self):
        # This test just verifies that the response headers/body
        # are in the debug log.  It's an integration test so that
        # we can refactor the code however we want, as long as we don't
        # lose this feature.
        session = yieldfrom.botocore.session.get_session()
        client = yield from session.create_client('s3', region_name='us-west-2')
        debug_log = StringIO()
        session.set_stream_logger('', logging.DEBUG, debug_log)
        yield from client.list_buckets()
        debug_log_contents = debug_log.getvalue()
        self.assertIn('Response headers', debug_log_contents)
        self.assertIn('Response body', debug_log_contents)


class TestAcceptedDateTimeFormats(unittest.TestCase):

    @asyncio.coroutine
    def set_up(self):
        self.session = yieldfrom.botocore.session.get_session()
        self.client = yield from self.session.create_client('emr', 'us-west-2')

    @async_test
    def test_accepts_datetime_object(self):
        response = yield from self.client.list_clusters(
            CreatedAfter=datetime.datetime.now())
        self.assertIn('Clusters', response)

    @async_test
    def test_accepts_epoch_format(self):
        response = yield from self.client.list_clusters(CreatedAfter=0)
        self.assertIn('Clusters', response)

    @async_test
    def test_accepts_iso_8601_unaware(self):
        response = yield from self.client.list_clusters(
            CreatedAfter='2014-01-01T00:00:00')
        self.assertIn('Clusters', response)

    @async_test
    def test_accepts_iso_8601_utc(self):
        response = yield from self.client.list_clusters(
            CreatedAfter='2014-01-01T00:00:00Z')
        self.assertIn('Clusters', response)

    @async_test
    def test_accepts_iso_8701_local(self):
        response = yield from self.client.list_clusters(
            CreatedAfter='2014-01-01T00:00:00-08:00')
        self.assertIn('Clusters', response)


class TestClientCanBeCloned(unittest.TestCase):
    def setUp(self):
        self.session = yieldfrom.botocore.session.get_session()

    @async_test
    def test_client_can_clone_with_service_events(self):
        # We should also be able to create a client object.
        client = yield from self.session.create_client('s3', region_name='us-west-2')
        # We really just want to ensure create_client doesn't raise
        # an exception, but we'll double check that the client looks right.
        self.assertTrue(hasattr(client, 'list_buckets'))

    def test_client_raises_exception_invalid_region(self):
        with self.assertRaisesRegexp(ValueError, 'Invalid endpoint'):
            yield from self.session.create_client('cloudformation',
                                       region_name='invalid region name')


class TestClientErrorMessages(unittest.TestCase):
    def test_region_mentioned_in_invalid_region(self):
        session = yieldfrom.botocore.session.get_session()
        client = yield from session.create_client(
            'cloudformation', region_name='bad-region-name')
        with self.assertRaisesRegexp(EndpointConnectionError,
                                     'Could not connect to the endpoint URL'):
            yield from client.list_stacks()


class TestClientMeta(unittest.TestCase):
    def setUp(self):
        self.session = yieldfrom.botocore.session.get_session()

    def test_region_name_on_meta(self):
        client = yield from self.session.create_client('s3', 'us-west-2')
        self.assertEqual(client.meta.region_name, 'us-west-2')

    def test_endpoint_url_on_meta(self):
        client = yield from self.session.create_client('s3', 'us-west-2',
                                            endpoint_url='https://foo')
        self.assertEqual(client.meta.endpoint_url, 'https://foo')


class TestClientInjection(unittest.TestCase):
    def setUp(self):
        self.session = yieldfrom.botocore.session.get_session()

    def test_can_inject_client_methods(self):

        def extra_client_method(self, name):
            return name

        def inject_client_method(class_attributes, **kwargs):
            class_attributes['extra_client_method'] = extra_client_method

        self.session.register('creating-client-class.s3',
                              inject_client_method)

        client = yield from self.session.create_client('s3', 'us-west-2')

        # We should now have access to the extra_client_method above.
        self.assertEqual(client.extra_client_method('foo'), 'foo')
