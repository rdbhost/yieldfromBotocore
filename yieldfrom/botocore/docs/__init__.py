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
import os, sys

#sys.path.append('../..')
from .service import ServiceDocumenter


def generate_docs(root_dir):
    """Generates the reference documentation for botocore

    This will go through every available AWS service and output ReSTructured
    text files documenting each service.

    :param root_dir: The directory to write the reference files to. Each
        service's reference documentation is loacated at
        root_dir/reference/services/service-name.rst
    """
    from yieldfrom.botocore import session as botosession

    services_doc_path = os.path.join(root_dir, 'reference', 'services')
    if not os.path.exists(services_doc_path):
        os.makedirs(services_doc_path)

    # Generate reference docs and write them out.
    session = botosession.get_session()
    for service_name in session.get_available_services():
        sd = ServiceDocumenter(service_name)
        yield from sd.create_client()
        docs = sd.document_service()
        service_doc_path = os.path.join(
            services_doc_path, service_name + '.rst')
        with open(service_doc_path, 'wb') as f:
            f.write(docs)
