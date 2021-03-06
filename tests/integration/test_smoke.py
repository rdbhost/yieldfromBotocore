"""Smoke tests to verify basic communication to all AWS services."""

# This file altered by David Keeney 2015, as part of conversion to
# asyncio.
#
import os
os.environ['PYTHONASYNCIODEBUG'] = '1'
import logging
logging.basicConfig(level=logging.DEBUG)

import mock
import sys
import asyncio

sys.path.append('..')
# from asyncio_test_utils import async_test

from pprint import pformat
import warnings
from nose.tools import assert_equals, assert_true

from yieldfrom.botocore import xform_name
import yieldfrom.botocore.session
from yieldfrom.botocore.client import ClientError
from yieldfrom.requests import adapters
from yieldfrom.requests.exceptions import ConnectionError


REGION = 'us-east-1'
# Mapping of service -> api calls to try.
# Each api call is a dict of OperationName->params.
# Empty params means that the operation will be called with no params.  This is
# used as a quick verification that we can successfully make calls to services.
SMOKE_TESTS = {
 'autoscaling': {'DescribeAccountLimits': {},
                 'DescribeAdjustmentTypes': {}},
 'cloudformation': {'DescribeStacks': {},
                    'ListStacks': {}},
 'cloudfront': {'ListDistributions': {},
                'ListStreamingDistributions': {}},
 'cloudhsm': {'ListAvailableZones': {}},
 'cloudsearch': {'DescribeDomains': {},
                 'ListDomainNames': {}},
 'cloudtrail': {'DescribeTrails': {}},
 'cloudwatch': {'ListMetrics': {}},
 'codedeploy': {'ListApplications': {}},
 'cognito-identity': {'ListIdentityPools': {'MaxResults': 1}},
 'cognito-sync': {'ListIdentityPoolUsage': {}},
 'config': {'DescribeDeliveryChannels': {}},
 'datapipeline': {'ListPipelines': {}},
 'directconnect': {'DescribeConnections': {}},
 'ds': {'DescribeDirectories': {}},
 'dynamodb': {'ListTables': {}},
 'ec2': {'DescribeRegions': {},
         'DescribeInstances': {}},
 'ecs': {'DescribeClusters': {}},
 'elasticache': {'DescribeCacheClusters': {}},
 'elasticbeanstalk': {'DescribeApplications': {}},
 'elastictranscoder': {'ListPipelines': {}},
 'elb': {'DescribeLoadBalancers': {}},
 'emr': {'ListClusters': {}},
 'glacier': {'ListVaults': {}},
 'iam': {'ListUsers': {}},
 # Does not work with session credentials so
 # importexport tests are not run.
 #'importexport': {'ListJobs': {}},
 'importexport': {},
 'kinesis': {'ListStreams': {}},
 'kms': {'ListKeys': {}},
 'lambda': {'ListFunctions': {}},
 'logs': {'DescribeLogGroups': {}},
 'machinelearning': {'DescribeMLModels': {}},
 'opsworks': {'DescribeStacks': {}},
 'rds': {'DescribeDBInstances': {}},
 'redshift': {'DescribeClusters': {}},
 'route53': {'ListHostedZones': {}},
 'route53domains': {'ListDomains': {}},
 's3': {'ListBuckets': {}},
 'sdb': {'ListDomains': {}},
 'ses': {'ListIdentities': {}},
 'sns': {'ListTopics': {}},
 'sqs': {'ListQueues': {}},
 'ssm': {'ListDocuments': {}},
 'storagegateway': {'ListGateways': {}},
 # sts tests would normally go here, but
 # there aren't any calls you can make when
 # using session credentials so we don't run any
 # sts tests.
 'sts': {},
 #'sts': {'GetSessionToken': {}},
 # Subscription needed for support API calls.
 'support': {},
 'swf': {'ListDomains': {'registrationStatus': 'REGISTERED'}},
 'workspaces': {'DescribeWorkspaces': {}},
}

# Same thing as the SMOKE_TESTS hash above, except these verify
# that we get an error response back from the server because
# we've sent invalid params.
ERROR_TESTS = {
    's3': {'ListObjects': {'Bucket': 'thisbucketdoesnotexistasdf'}},
    'dynamodb': {'DescribeTable': {'TableName': 'unknowntablefoo'}},
    'sns': {'ConfirmSubscription': {'TopicArn': 'a', 'Token': 'b'}},
}

def test_can_make_request_with_client():
    # Same as test_can_make_request, but with Client objects
    # instead of service/operations.
    session = yieldfrom.botocore.session.get_session()
    for service_name in SMOKE_TESTS:
        client = yield from session.create_client(service_name, region_name=REGION)
        for operation_name in SMOKE_TESTS[service_name]:
            kwargs = SMOKE_TESTS[service_name][operation_name]
            method_name = xform_name(operation_name)
            yield _make_client_call, client, method_name, kwargs


def _make_client_call(client, operation_name, kwargs):
    method = getattr(client, operation_name)
    with warnings.catch_warnings(record=True) as caught_warnings:
        response = method(**kwargs)
        assert_equals(len(caught_warnings), 0,
                      "Warnings were emitted during smoke test: %s"
                      % caught_warnings)
        assert_true('Errors' not in response)


def test_can_make_request_and_understand_errors_with_client():
    session = yieldfrom.botocore.session.get_session()
    for service_name in ERROR_TESTS:
        client = yield from session.create_client(service_name, region_name=REGION)
        for operation_name in ERROR_TESTS[service_name]:
            kwargs = ERROR_TESTS[service_name][operation_name]
            method_name = xform_name(operation_name)
            yield _make_error_client_call, client, method_name, kwargs


def _make_error_client_call(client, operation_name, kwargs):
    method = getattr(client, operation_name)
    try:
        response = method(**kwargs)
    except ClientError as e:
        pass
    else:
        raise AssertionError("Expected client error was not raised "
                             "for %s.%s" % (client, operation_name))


@asyncio.coroutine
def test_client_can_retry_request_properly():
    session = yieldfrom.botocore.session.get_session()
    for service_name in SMOKE_TESTS:
        client = yield from session.create_client(service_name, region_name=REGION)
        for operation_name in SMOKE_TESTS[service_name]:
            kwargs = SMOKE_TESTS[service_name][operation_name]
            yield (_make_client_call_with_errors, client,
                   operation_name, kwargs)


@asyncio.coroutine
def _make_client_call_with_errors(client, operation_name, kwargs):
    operation = getattr(client, xform_name(operation_name))
    original_send = adapters.HTTPAdapter.send
    def mock_http_adapter_send(self, *args, **kwargs):
        if not getattr(self, '_integ_test_error_raised', False):
            self._integ_test_error_raised = True
            raise ConnectionError("Simulated ConnectionError raised.")
        else:
            return (yield from original_send(self, *args, **kwargs))
    with mock.patch('yieldfrom.botocore.vendored.requests.adapters.HTTPAdapter.send',
                    mock_http_adapter_send):
        try:
            response = operation(**kwargs)
        except ClientError as e:
            assert False, ('Request was not retried properly, '
                           'received error:\n%s' % pformat(e))
