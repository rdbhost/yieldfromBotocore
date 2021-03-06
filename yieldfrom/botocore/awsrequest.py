# Copyright (c) 2012-2013 Mitch Garnaat http://garnaat.org/
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
import sys
import logging
import functools
import inspect
import asyncio

from yieldfrom.requests import models
from yieldfrom.requests.sessions import REDIRECT_STATI
from .compat import HTTPHeaders, HTTPResponse, urlunsplit, urlsplit
from .utils import percent_encode_sequence
from .exceptions import UnseekableStreamError
from yieldfrom.urllib3.connection import VerifiedHTTPSConnection
from yieldfrom.urllib3.connection import HTTPConnection
from yieldfrom.urllib3.connectionpool import HTTPConnectionPool
from yieldfrom.urllib3.connectionpool import HTTPSConnectionPool

from . import request_sessions_fixer

logger = logging.getLogger(__name__)


class AWSHTTPResponse(HTTPResponse):
    # The *args, **kwargs is used because the args are slightly
    # different in py2.6 than in py2.7/py3.
    def __init__(self, *args, **kwargs):
        self._status_tuple = kwargs.pop('status_tuple')
        HTTPResponse.__init__(self, *args, **kwargs)

    @asyncio.coroutine
    def _read_status(self):
        if self._status_tuple is not None:
            status_tuple = self._status_tuple
            self._status_tuple = None
            return status_tuple
        else:
            return (yield from HTTPResponse._read_status(self))


class AWSHTTPConnection(HTTPConnection):
    """HTTPConnection that supports Expect 100-continue.

    This is conceptually a subclass of httplib.HTTPConnection (though
    technically we subclass from urllib3, which subclasses
    httplib.HTTPConnection) and we only override this class to support Expect
    100-continue, which we need for S3.  As far as I can tell, this is
    general purpose enough to not be specific to S3, but I'm being
    tentative and keeping it in botocore because I've only tested
    this against AWS services.

    """
    def __init__(self, *args, **kwargs):
        HTTPConnection.__init__(self, *args, **kwargs)
        self._original_response_cls = self.response_class
        # We'd ideally hook into httplib's states, but they're all
        # __mangled_vars so we use our own state var.  This variable is set
        # when we receive an early response from the server.  If this value is
        # set to True, any calls to send() are noops.  This value is reset to
        # false every time _send_request is called.  This is to workaround the
        # fact that py2.6 (and only py2.6) has a separate send() call for the
        # body in _send_request, as opposed to endheaders(), which is where the
        # body is sent in all versions > 2.6.
        self._response_received = False

    @asyncio.coroutine
    def _send_request(self, method, url, body, headers):
        self._response_received = False
        if headers.get('Expect', b'') == b'100-continue':
            self._expect_header_set = True
        else:
            self._expect_header_set = False
            self.response_class = self._original_response_cls
        rval = yield from HTTPConnection._send_request(
            self, method, url, body, headers)
        self._expect_header_set = False
        return rval

    def _convert_to_bytes(self, mixed_buffer):
        # Take a list of mixed str/bytes and convert it
        # all into a single bytestring.
        # Any six.text_types will be encoded as utf-8.
        bytes_buffer = []
        for chunk in mixed_buffer:
            if isinstance(chunk, str):
                bytes_buffer.append(chunk.encode('utf-8'))
            else:
                bytes_buffer.append(chunk)
        msg = b"\r\n".join(bytes_buffer)
        return msg

    @asyncio.coroutine
    def _send_output(self, message_body=None):
        self._buffer.extend((b"", b""))
        msg = self._convert_to_bytes(self._buffer)
        del self._buffer[:]
        # If msg and message_body are sent in a single send() call,
        # it will avoid performance problems caused by the interaction
        # between delayed ack and the Nagle algorithm.
        if isinstance(message_body, bytes):
            msg += message_body
            message_body = None
        yield from self.send(msg)
        if self._expect_header_set:
            # This is our custom behavior.  If the Expect header was
            # set, it will trigger this custom behavior.
            logger.debug("Waiting for 100 Continue response.")

            # Wait for 1 second for the server to send a response.
            read = yield from asyncio.wait_for(self.notSock.readexactly(1), 1.0)
            if read:
                yield from self._handle_expect_response(read, message_body)
                return
            else:
                # From the RFC:
                # Because of the presence of older implementations, the
                # protocol allows ambiguous situations in which a client may
                # send "Expect: 100-continue" without receiving either a 417
                # (Expectation Failed) status or a 100 (Continue) status.
                # Therefore, when a client sends this header field to an origin
                # server (possibly via a proxy) from which it has never seen a
                # 100 (Continue) status, the client SHOULD NOT wait for an
                # indefinite period before sending the request body.
                logger.debug("No response seen from server, continuing to "
                             "send the response body.")
        if message_body is not None:
            # message_body was not a string (i.e. it is a file), and
            # we must run the risk of Nagle.
            yield from self.send(message_body)

    @asyncio.coroutine
    def _consume_headers(self, fp):
        # Most servers (including S3) will just return
        # the CLRF after the 100 continue response.  However,
        # some servers (I've specifically seen this for squid when
        # used as a straight HTTP proxy) will also inject a
        # Connection: keep-alive header.  To account for this
        # we'll read until we read '\r\n', and ignore any headers
        # that come immediately after the 100 continue response.
        current = None
        while current != b'\r\n':
            current = yield from fp.readline()

    @asyncio.coroutine
    def _handle_expect_response(self, begin, message_body):
        # This is called when we sent the request headers containing
        # an Expect: 100-continue header and received a response.
        # We now need to figure out what to do.
        fp = self.notSock  # self.sock.makefile('rb', 0)
        try:
            maybe_status_line = yield from fp.readline()
            maybe_status_line = begin + maybe_status_line
            parts = maybe_status_line.split(None, 2)
            if self._is_100_continue_status(maybe_status_line):
                yield from self._consume_headers(fp)
                logger.debug("100 Continue response seen, "
                             "now sending request body.")
                yield from self._send_message_body(message_body)
            elif len(parts) == 3 and parts[0].startswith(b'HTTP/'):
                # From the RFC:
                # Requirements for HTTP/1.1 origin servers:
                #
                # - Upon receiving a request which includes an Expect
                #   request-header field with the "100-continue"
                #   expectation, an origin server MUST either respond with
                #   100 (Continue) status and continue to read from the
                #   input stream, or respond with a final status code.
                #
                # So if we don't get a 100 Continue response, then
                # whatever the server has sent back is the final response
                # and don't send the message_body.
                logger.debug("Received a non 100 Continue response "
                             "from the server, NOT sending request body.")
                status_tuple = (parts[0].decode('ascii'),
                                int(parts[1]), parts[2].decode('ascii'))
                response_class = functools.partial(
                    AWSHTTPResponse, status_tuple=status_tuple)
                self.response_class = response_class
                self._response_received = True
        finally:
            #fp.close()
            pass

    @asyncio.coroutine
    def _send_message_body(self, message_body):
        if message_body is not None:
            yield from self.send(message_body)

    @asyncio.coroutine
    def send(self, str):
        if self._response_received:
            logger.debug("send() called, but response already received. "
                         "Not sending data.")
            return
        _r = yield from HTTPConnection.send(self, str)
        return _r

    def _is_100_continue_status(self, maybe_status_line):
        parts = maybe_status_line.split(None, 2)
        # Check for HTTP/<version> 100 Continue\r\n
        return (
            len(parts) >= 3 and parts[0].startswith(b'HTTP/') and
            parts[1] == b'100')


class AWSHTTPSConnection(VerifiedHTTPSConnection):

    def __init__(self, *args, **kws):
        VerifiedHTTPSConnection.__init__(self, *args, **kws)
        self._original_response_cls = self.response_class
        self._response_received = False


# Now we need to set the methods we overrode from AWSHTTPConnection
# onto AWSHTTPSConnection.  This is just a shortcut to avoid
# copy/pasting the same code into AWSHTTPSConnection.
for name, function in AWSHTTPConnection.__dict__.items():
    if inspect.isfunction(function) and name != '__init__':
        setattr(AWSHTTPSConnection, name, function)


def prepare_request_dict(request_dict, endpoint_url, user_agent=None):
    """
    This method prepares a request dict to be created into an
    AWSRequestObject. This prepares the request dict by adding the
    url and the user agent to the request dict.

    :type request_dict: dict
    :param request_dict:  The request dict (created from the
        ``serialize`` module).

    :type user_agent: string
    :param user_agent: The user agent to use for this request.

    :type endpoint_url: string
    :param endpoint_url: The full endpoint url, which contains at least
        the scheme, the hostname, and optionally any path components.
    """
    r = request_dict
    if user_agent is not None:
        headers = r['headers']
        headers['User-Agent'] = user_agent
    url = _urljoin(endpoint_url, r['url_path'])
    if r['query_string']:
        encoded_query_string = percent_encode_sequence(r['query_string'])
        if '?' not in url:
            url += '?%s' % encoded_query_string
        else:
            url += '&%s' % encoded_query_string
    r['url'] = url


def create_request_object(request_dict):
    """
    This method takes a request dict and creates an AWSRequest object
    from it.

    :type request_dict: dict
    :param request_dict:  The request dict (created from the
        ``prepare_request_dict`` method).

    :rtype: ``botocore.awsrequest.AWSRequest``
    :return: An AWSRequest object based on the request_dict.

    """
    r = request_dict
    return AWSRequest(method=r['method'], url=r['url'],
                      data=r['body'],
                      headers=r['headers'])


def _urljoin(endpoint_url, url_path):
    p = urlsplit(endpoint_url)
    # <part>   - <index>
    # scheme   - p[0]
    # netloc   - p[1]
    # path     - p[2]
    # query    - p[3]
    # fragment - p[4]
    if not url_path or url_path == '/':
        # If there's no path component, ensure the URL ends with
        # a '/' for backwards compatibility.
        if not p[2]:
            return endpoint_url + '/'
        return endpoint_url
    if p[2].endswith('/') and url_path.startswith('/'):
        new_path = p[2][:-1] + url_path
    else:
        new_path = p[2] + url_path
    reconstructed = urlunsplit((p[0], p[1], new_path, p[3], p[4]))
    return reconstructed


class AWSRequest(models.RequestEncodingMixin, models.Request):
    def __init__(self, *args, **kwargs):
        self.auth_path = None
        if 'auth_path' in kwargs:
            self.auth_path = kwargs['auth_path']
            del kwargs['auth_path']
        models.Request.__init__(self, *args, **kwargs)
        headers = HTTPHeaders()
        if self.headers is not None:
            for key, value in self.headers.items():
                headers[key] = value
        self.headers = headers
        # This is a dictionary to hold information that is used when
        # processing the request. What is inside of ``context`` is open-ended.
        # For example, it may have a timestamp key that is used for holding
        # what the timestamp is when signing the request. Note that none
        # of the information that is inside of ``context`` is directly
        # sent over the wire; the information is only used to assist in
        # creating what is sent over the wire.
        self.context = {}

    def prepare(self):
        """Constructs a :class:`AWSPreparedRequest <AWSPreparedRequest>`."""
        # Eventually I think it would be nice to add hooks into this process.
        p = AWSPreparedRequest(self)
        p.prepare_method(self.method)
        p.prepare_url(self.url, self.params)
        p.prepare_headers(self.headers)
        p.prepare_cookies(self.cookies)
        p.prepare_body(self.data, self.files)
        p.prepare_auth(self.auth)
        return p

    @property
    def body(self):
        p = models.PreparedRequest()
        p.prepare_headers({})
        p.prepare_body(self.data, self.files)
        return p.body


class AWSPreparedRequest(models.PreparedRequest):
    """Represents a prepared request.

    :ivar method: HTTP Method
    :ivar url: The full url
    :ivar headers: The HTTP headers to send.
    :ivar body: The HTTP body.
    :ivar hooks: The set of callback hooks.

    In addition to the above attributes, the following attributes are
    available:

    :ivar query_params: The original query parameters.
    :ivar post_param: The original POST params (dict).

    """
    def __init__(self, original_request):
        self.original = original_request
        super(AWSPreparedRequest, self).__init__()
        self.hooks.setdefault('response', []).append(
            self.reset_stream_on_redirect)

    @asyncio.coroutine
    def reset_stream_on_redirect(self, response, **kwargs):
        yield None
        if response.status_code in REDIRECT_STATI and \
                self._looks_like_file(self.body):
            logger.debug("Redirect received, rewinding stream: %s", self.body)
            self.reset_stream()

    def _looks_like_file(self, body):
        return hasattr(body, 'read') and hasattr(body, 'seek')

    def reset_stream(self):
        # Trying to reset a stream when there is a no stream will
        # just immediately return.  It's not an error, it will produce
        # the same result as if we had actually reset the stream (we'll send
        # the entire body contents again if we need to).
        # Same case if the body is a string/bytes type.
        if self.body is None or isinstance(self.body, str) or \
                isinstance(self.body, bytes):
            return
        try:
            logger.debug("Rewinding stream: %s", self.body)
            self.body.seek(0)
        except Exception as e:
            logger.debug("Unable to rewind stream: %s", e)
            raise UnseekableStreamError(stream_object=self.body)


HTTPSConnectionPool.ConnectionCls = AWSHTTPSConnection
HTTPConnectionPool.ConnectionCls = AWSHTTPConnection
