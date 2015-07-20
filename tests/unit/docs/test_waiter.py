# Copyright 2015 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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
import sys
import asyncio

sys.path.extend(['..', '../..'])
from asyncio_test_utils import async_test
from docs import BaseDocsTest
from yieldfrom.botocore.docs.waiter import WaiterDocumenter
from yieldfrom.botocore.waiter import WaiterModel


class TestWaiterDocumenter(BaseDocsTest):
    @asyncio.coroutine
    def set_up(self):
        super(TestWaiterDocumenter, self).setUp()
        self.add_shape_to_params('Biz', 'String')
        yield from self.setup_client()
        waiter_model = WaiterModel(self.waiter_json_model)
        self.waiter_documenter = WaiterDocumenter(
            client=self.client, service_waiter_model=waiter_model)

    @async_test
    def test_document_waiters(self):
        self.waiter_documenter.document_waiters(
            self.doc_structure)
        self.assert_contains_lines_in_order([
            '=======',
            'Waiters',
            '=======',
            'The available waiters are:',
            '* :py:class:`myservice.Waiter.sample_operation_complete`',
            '.. py:class:: myservice.Waiter.sample_operation_complete',
            '  ::',
            '    waiter = client.get_waiter(\'sample_operation_complete\')',
            '  .. py:method:: wait(Biz=None)',
            ('    This polls :py:meth:`myservice.Client.sample_operation` '
             'every 15 seconds until a successful state is reached. An error '
             'is returned after 40 failed checks.'),
            '    **Request Syntax**',
            '    ::',
            '      waiter.wait(',
            '          Biz=\'string\'',
            '      )',
            '    :type Biz: string',
            '    :param Biz:',
            '    :returns: None'
        ])
