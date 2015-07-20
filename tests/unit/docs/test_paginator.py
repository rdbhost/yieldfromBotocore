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
sys.path.extend(['../..', '..'])
from asyncio_test_utils import async_test

from docs import BaseDocsTest
from yieldfrom.botocore.docs.paginator import PaginatorDocumenter
from yieldfrom.botocore.paginate import PaginatorModel


class TestPaginatorDocumenter(BaseDocsTest):
    @asyncio.coroutine
    def set_up(self):
        super(TestPaginatorDocumenter, self).setUp()
        yield from self.setup_client()
        self.add_shape_to_params('Biz', 'String')
        yield from self.extra_setup()

    @asyncio.coroutine
    def extra_setup(self):
        yield from self.setup_client()
        paginator_model = PaginatorModel(self.paginator_json_model)
        self.paginator_documenter = PaginatorDocumenter(
            client=self.client, service_paginator_model=paginator_model)

    @async_test
    def test_document_paginators(self):
        self.paginator_documenter.document_paginators(
            self.doc_structure)
        self.assert_contains_lines_in_order([
            '==========',
            'Paginators',
            '==========',
            'The available paginators are:',
            '* :py:class:`myservice.Paginator.sample_operation`',
            '.. py:class:: myservice.Paginator.sample_operation',
            '  ::',
            '    paginator = client.get_paginator(\'sample_operation\')',
            '  .. py:method:: paginate(Biz=None, PaginationConfig=None)',
            ('    Creates an iterator that will paginate through responses'
             ' from :py:meth:`myservice.Client.sample_operation`.'),
            '    **Request Syntax**',
            '    ::',
            '      response_iterator = paginator.paginate(',
            '          Biz=\'string\',',
            '          PaginationConfig={',
            '              \'MaxItems\': 123,',
            '              \'PageSize\': 123,',
            '              \'StartingToken\': \'string\'',
            '          }',
            '      )',
            '    :type Biz: string',
            '    :param Biz:',
            '    :type PaginationConfig: dict',
            '    :param PaginationConfig:',
            ('      A dictionary that provides parameters to '
             'control pagination.'),
            '      - **MaxItems** *(integer) --*',
            '      - **PageSize** *(integer) --*',
            '      - **StartingToken** *(string) --*',
            '    :rtype: dict',
            '    :returns:',
            '      **Response Syntax**',
            '      ::',
            '        {',
            '            \'Biz\': \'string\',',
            '            \'NextToken\': \'string\'',
            '        }',
            '      **Response Structure**',
            '      - *(dict) --*',
            '        - **Biz** *(string) --*',
            '        - **NextToken** *(string) --*'
        ])
