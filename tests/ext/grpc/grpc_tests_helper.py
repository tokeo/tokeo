"""
Shared parts of the tests of tokeo.ext.grpc: the test app and the echo service
with its interceptor chain.

The extension imports the servicer, the function that adds it to the server
and the interceptor chain by the module paths of its config, so they live
here as an importable module. The service is built from grpc generic
handlers on raw bytes, no .proto file and no generated code are needed.
"""

import grpc
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest

#: The full name of the echo service, a method is called as /SERVICE/<name>
SERVICE = 'tokeo.tests.Echo'

#: The config values that wire the echo service into the extension
ADD_SERVICER = 'tests.ext.grpc.grpc_tests_helper:add_echo_servicer_to_server'
SERVICER = 'tests.ext.grpc.grpc_tests_helper:EchoServicer'
INTERCEPTORS = 'tests.ext.grpc.grpc_tests_helper:token_interceptors'


class EchoServicer:
    """Answers Say with the request behind echo, Peer with the client identities."""

    def Say(self, request, context):
        return b'echo ' + request

    def Peer(self, request, context):
        return b','.join(context.peer_identities() or [])


def add_echo_servicer_to_server(servicer, server):
    # the counterpart of a generated add_<Service>Servicer_to_server
    handlers = {
        'Say': grpc.unary_unary_rpc_method_handler(servicer.Say),
        'Peer': grpc.unary_unary_rpc_method_handler(servicer.Peer),
    }
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(SERVICE, handlers),))


def _deny(request, context):
    # the handler for every call the interceptor refuses
    context.abort(grpc.StatusCode.UNAUTHENTICATED, 'missing x-token')


class TokenInterceptor(grpc.ServerInterceptor):
    """Refuses every call without the metadata x-token: secret."""

    def intercept_service(self, continuation, handler_call_details):
        if ('x-token', 'secret') in tuple(handler_call_details.invocation_metadata or ()):
            return continuation(handler_call_details)
        return grpc.unary_unary_rpc_method_handler(_deny)


def token_interceptors():
    # the factory grpc_interceptor names, it returns the interceptor chain
    return [TokenInterceptor()]


class GrpcTest(TokeoTest):
    """A test app serving the echo service by grpc, with further settings."""

    class Meta:
        extensions = [
            'tokeo.ext.print',
            'tokeo.ext.grpc',
        ]

    def __init__(self, settings=None, **kw):
        config = init_defaults('grpc')
        config['grpc'].update(proto_add_servicer_to_server=ADD_SERVICER, grpc_servicer=SERVICER)
        config['grpc'].update(settings or {})
        super().__init__(config_defaults=config, **kw)
