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
from .shape import ShapeDocumenter
from .utils import py_type_name


class BaseParamsDocumenter(ShapeDocumenter):
    def document_params(self, section, shape, include=None, exclude=None):
        """Fills out the documentation for a section given a model shape.

        :param section: The section to write the documentation to.

        :param shape: The shape of the operation.

        :type include: Dictionary where keys are parameter names and
            values are the shapes of the parameter names.
        :param include: The parameter shapes to include in the documentation.

        :type exclude: List of the names of the parameters to exclude.
        :param exclude: The names of the parameters to exclude from
            documentation.
        """
        history = []
        self.traverse_and_document_shape(
            section=section, shape=shape, history=history,
            name=None, include=include, exclude=exclude)

    def document_recursive_shape(self, section, shape, **kwargs):
        self._add_member_documentation(section, shape, **kwargs)

    def document_shape_default(self, section, shape, history, include=None,
                               exclude=None, **kwargs):
        self._add_member_documentation(section, shape, **kwargs)

    def document_shape_type_list(self, section, shape, history, include=None,
                                 exclude=None, **kwargs):
        self._add_member_documentation(section, shape, **kwargs)
        param_shape = shape.member
        param_section = section.add_new_section(param_shape.name)
        self._start_nested_param(param_section)
        self.traverse_and_document_shape(
            section=param_section, shape=param_shape,
            history=history, name=None)
        section = section.add_new_section('end-list')
        self._end_nested_param(section)

    def document_shape_type_map(self, section, shape, history, include=None,
                                exclude=None, **kwargs):
        self._add_member_documentation(section, shape, **kwargs)

        key_section = section.add_new_section('key')
        self._start_nested_param(key_section)
        self._add_member_documentation(key_section, shape.key)

        param_section = section.add_new_section(shape.value.name)
        param_section.style.indent()
        self._start_nested_param(param_section)
        self.traverse_and_document_shape(
            section=param_section, shape=shape.value,
            history=history, name=None)

        end_section = section.add_new_section('end-map')
        self._end_nested_param(end_section)
        self._end_nested_param(end_section)

    def document_shape_type_structure(self, section, shape, history,
                                      include=None, exclude=None,
                                      name=None, **kwargs):
        members = self._add_members_to_shape(shape.members, include)
        self._add_member_documentation(section, shape, name=name)
        for param in members:
            if exclude and param in exclude:
                continue
            param_section = section.add_new_section(param)
            self._start_nested_param(param_section)
            param_shape = members[param]
            self.traverse_and_document_shape(
                section=param_section, shape=param_shape,
                history=history, name=param)
        section = section.add_new_section('end-structure')
        self._end_nested_param(section)

    def _add_member_documentation(self, section, shape, **kwargs):
        pass

    def _add_members_to_shape(self, members, include):
        if include:
            members = members.copy()
            for param in include:
                members[param.name] = param
        return members

    def _start_nested_param(self, section):
        section.style.indent()
        section.style.new_line()

    def _end_nested_param(self, section):
        section.style.dedent()
        section.style.new_line()


class ResponseParamsDocumenter(BaseParamsDocumenter):
    """Generates the description for the response parameters"""

    EVENT_NAME = 'response-params'


    def _add_member_documentation(self, section, shape, name=None, **kwargs):
        py_type = py_type_name(shape.type_name)
        name_section = section.add_new_section('param-name')
        name_section.write('- ')
        if name is not None:
            name_section.style.bold('%s ' % name)
        name_section.style.italics('(%s) -- ' % py_type)

        documentation_section = section.add_new_section('param-documentation')
        if shape.documentation:
            documentation_section.style.indent()
            documentation_section.include_doc_string(shape.documentation)
        section.style.new_paragraph()


class RequestParamsDocumenter(BaseParamsDocumenter):
    """Generates the description for the request parameters"""

    EVENT_NAME = 'request-params'


    def document_shape_type_structure(self, section, shape, history,
                                      include=None, exclude=None, **kwargs):
        if len(history) > 1:
            self._add_member_documentation(section, shape, **kwargs)
            section.style.indent()
        members = self._add_members_to_shape(shape.members, include)
        for i, param in enumerate(members):
            if exclude and param in exclude:
                continue
            param_section = section.add_new_section(param)
            param_section.style.new_line()
            param_shape = members[param]
            is_required = param in shape.required_members
            self.traverse_and_document_shape(
                section=param_section, shape=param_shape,
                history=history, name=param, is_required=is_required)
        section = section.add_new_section('end-structure')
        if len(history) > 1:
            section.style.dedent()
        section.style.new_line()

    def _add_member_documentation(self, section, shape, name=None,
                                  is_top_level_param=False, is_required=False,
                                  **kwargs):
        py_type = py_type_name(shape.type_name)
        if is_top_level_param:
            type_section = section.add_new_section('param-type')
            type_section.write(':type %s: %s' % (name, py_type))
            end_type_section = type_section.add_new_section('end-param-type')
            end_type_section.style.new_line()
            name_section = section.add_new_section('param-name')
            name_section.write(':param %s: ' % name)

        else:
            name_section = section.add_new_section('param-name')
            name_section.write('- ')
            if name is not None:
                name_section.style.bold('%s ' % name)
            name_section.style.italics('(%s) -- ' % py_type)

        if is_required:
            is_required_section = section.add_new_section('is-required')
            is_required_section.style.indent()
            is_required_section.style.bold('[REQUIRED] ')
        if shape.documentation:
            documentation_section = section.add_new_section(
                'param-documentation')
            documentation_section.style.indent()
            documentation_section.include_doc_string(shape.documentation)
        end_param_section = section.add_new_section('end-param')
        end_param_section.style.new_paragraph()